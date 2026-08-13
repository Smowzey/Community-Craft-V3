import os
import hashlib
import requests
import threading
import urllib.parse
import zipfile
import time
from concurrent.futures import ThreadPoolExecutor

MODPACK_URL = "https://raw.githubusercontent.com/Smowzey/community-craft-v3/main/modpack.json"
GAME_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), ".community-craft-v3")
MODS_DIR = os.path.join(GAME_DIR, "mods")
RESOURCEPACKS_DIR = os.path.join(GAME_DIR, "resourcepacks")
SHADERPACKS_DIR = os.path.join(GAME_DIR, "shaderpacks")
CONFIG_DIR = os.path.join(GAME_DIR, "config")

# Hôtes autorisés pour les téléchargements. Le modpack.json est distant : sans
# cette liste, quiconque pourrait le modifier pourrait faire télécharger
# n'importe quel fichier depuis n'importe quel serveur.
ALLOWED_HOSTS = (
    "edge.forgecdn.net",
    "mediafilez.forgecdn.net",
    "media.forgecdn.net",
    "api.curseforge.com",
    "cdn.modrinth.com",
    "api.modrinth.com",
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
)

# ⚠️ Cette clé a été publiée en clair sur le dépôt GitHub : elle est compromise.
# Elle ne sert plus que de secours pour ne pas casser les installations existantes.
# Génère-en une nouvelle sur console.curseforge.com puis mets-la soit dans la
# variable d'environnement CURSEFORGE_API_KEY, soit dans un fichier
# curseforge_key.txt (à côté du launcher ou dans le dossier du jeu).
_FALLBACK_API_KEY = "$2a$10$ogJO1kKcvpUth60qurFiaeaJ8vjDyk3Z0v2W54oXt/cbyi2gbpSvy"


def _load_api_key() -> str:
    """Récupère la clé CurseForge sans la coder en dur dans le dépôt."""
    key = (os.environ.get("CURSEFORGE_API_KEY") or "").strip()
    if key:
        return key
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "curseforge_key.txt"),
        os.path.join(GAME_DIR, "curseforge_key.txt"),
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    key = f.read().strip()
                if key:
                    return key
        except OSError:
            pass
    return _FALLBACK_API_KEY


CURSEFORGE_API_KEY = _load_api_key()


def safe_filename(raw_name: str, fallback: str = "fichier_inconnu") -> str:
    """Nettoie un nom de fichier venant du modpack.json distant.

    Empêche l'écriture hors du dossier cible (« ../../Démarrage/virus.exe ») et
    les noms réservés Windows. On ne garde que le nom de fichier final.
    """
    name = os.path.basename(str(raw_name or "").replace("\\", "/").strip())
    # Caractères interdits sous Windows + tout ce qui reste de suspect
    for bad in '<>:"|?*\0':
        name = name.replace(bad, "_")
    name = name.strip(" .")
    if not name or name in (".", ".."):
        return fallback
    reserved = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
    if name.split(".")[0].lower() in reserved:
        name = "_" + name
    return name[:180]


def check_download_url(url: str) -> str:
    """Valide une URL de téléchargement : HTTPS uniquement + hôte connu."""
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme != "https":
        raise Exception(f"URL refusée (HTTPS obligatoire) : {url}")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise Exception(f"URL refusée (hôte non autorisé : {host})")
    return url


def fetch_modpack() -> dict:
    # On ajoute un timestamp à l'URL pour empêcher GitHub de nous donner un vieux fichier en cache
    url_sans_cache = f"{MODPACK_URL}?t={int(time.time())}"
    response = requests.get(url_sans_cache, timeout=10)
    if response.status_code == 404:
        raise Exception("modpack.json introuvable sur GitHub (Dépôt privé ou mauvaise branche ?)")
    elif response.status_code != 200:
        raise Exception(f"Erreur HTTP {response.status_code} en récupérant le modpack.")
    return response.json()


def sha256_of_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_curseforge_download_url(project_id: int, file_id: int, filename: str) -> str:
    """Récupère l'URL de téléchargement depuis l'API CurseForge."""
    headers = {"x-api-key": CURSEFORGE_API_KEY}
    url = f"https://api.curseforge.com/v1/mods/{project_id}/files/{file_id}/download-url"
    response = requests.get(url, headers=headers, timeout=10)
    
    if response.status_code == 200:
        data = response.json().get("data")
        if data:
            return data
        
    # Si l'auteur a bloqué les téléchargements tiers, on recrée l'URL manuellement (Edge CDN)
    file_id_str = str(int(file_id))
    # Découpage correct pour les ID à 6 ou 7 chiffres (ex: 1234567 -> 1234 / 567)
    part1 = file_id_str[:-3]
    part2 = file_id_str[-3:]
    return f"https://edge.forgecdn.net/files/{part1}/{part2}/{urllib.parse.quote(filename)}"


def download_file_item(item: dict, target_dir: str, default_ext: str, on_progress=None):
    os.makedirs(target_dir, exist_ok=True)

    # On s'assure d'avoir au moins un nom de fichier, sinon on utilise le nom par défaut.
    # Le nom vient d'un fichier distant : on le nettoie avant de l'utiliser sur le disque.
    raw_name = item.get("filename") or (str(item.get("name", "fichier_inconnu")) + default_ext)
    filename = safe_filename(raw_name, "fichier_inconnu" + default_ext)
    filepath = os.path.join(target_dir, filename)

    # Ceinture et bretelles : le fichier doit rester dans le dossier cible
    if os.path.dirname(os.path.abspath(filepath)) != os.path.abspath(target_dir):
        raise Exception(f"Nom de fichier refusé : {raw_name!r}")

    expected_sha = str(item.get("sha256", "") or "").strip().lower()

    # Déjà installé, intègre et à jour ?
    if os.path.exists(filepath):
        # Fichier valide (vrai JAR/ZIP) ET (pas de sha attendu OU sha identique) -> on garde
        if zipfile.is_zipfile(filepath) and (not expected_sha or sha256_of_file(filepath) == expected_sha):
            if on_progress:
                on_progress(item.get("name", filename), "ok")
            return
        # Corrompu ou empreinte différente -> on supprime pour retélécharger proprement
        try:
            os.remove(filepath)
        except:
            pass

    if on_progress:
        on_progress(item.get("name", filename), "downloading")

    # On vérifie si une URL directe est fournie, sinon on utilise l'API CurseForge
    download_url = item.get("url")

    if not download_url:
        if "curseforge_project_id" in item and "curseforge_file_id" in item:
            download_url = get_curseforge_download_url(
                item["curseforge_project_id"],
                item["curseforge_file_id"],
                filename
            )
        else:
            raise Exception(f"Le fichier '{item.get('name', filename)}' n'a pas d'ID CurseForge ni d'URL dans modpack.json.")

    # L'URL vient d'un fichier distant : on vérifie qu'elle est en HTTPS et qu'elle
    # pointe bien vers un hôte connu avant d'écrire quoi que ce soit sur le disque.
    download_url = check_download_url(download_url)

    # On ajoute une fausse "carte d'identité" (User-Agent) pour que Modrinth ne nous bloque pas
    headers = {"User-Agent": "CommunityCraft-Launcher/3.0"}

    # Téléchargement avec jusqu'à 3 essais (réseau instable) + vérification d'intégrité
    last_error = None
    for attempt in range(1, 4):
        try:
            with requests.get(download_url, stream=True, timeout=60, headers=headers) as response:
                response.raise_for_status()
                with open(filepath, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

            # Le fichier téléchargé est-il un zip/jar valide ?
            if not zipfile.is_zipfile(filepath):
                raise Exception("fichier corrompu (ce n'est pas un zip/jar valide)")
            # L'empreinte correspond-elle (si fournie) ?
            if expected_sha and sha256_of_file(filepath) != expected_sha:
                raise Exception("empreinte sha256 incorrecte")

            if on_progress:
                on_progress(item.get("name", filename), "done")
            return
        except Exception as e:
            last_error = e
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except:
                pass
            if attempt < 3:
                time.sleep(1.5 * attempt)  # petite pause croissante avant de réessayer

    raise Exception(f"Échec du téléchargement de {filename} après 3 essais : {last_error}")


def validate_mods() -> list:
    """Renvoie la liste des .jar du dossier mods qui sont corrompus ou vides.
    Utilisé pour un contrôle rapide avant de lancer le jeu (#6)."""
    corrupt = []
    if not os.path.exists(MODS_DIR):
        return corrupt
    for file in os.listdir(MODS_DIR):
        if not file.lower().endswith(".jar"):
            continue
        path = os.path.join(MODS_DIR, file)
        try:
            if os.path.getsize(path) == 0 or not zipfile.is_zipfile(path):
                corrupt.append(file)
        except OSError:
            corrupt.append(file)
    return corrupt


def _read_properties_lines(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().splitlines()


def _write_properties(path: str, lines: list, updates: dict):
    """Réécrit un fichier .properties en ne changeant que les clés demandées."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seen = set()
    out = []
    for line in lines:
        if "=" in line and not line.startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    if not out or not out[0].startswith("#"):
        out.insert(0, "#Managed by Community Craft Launcher")

    # Écriture atomique : évite un fichier de config à moitié écrit si ça coupe
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)


def set_shaders_enabled(enabled: bool):
    """Active / désactive les shaders (Oculus) sans toucher au shader choisi.

    Couper les shaders est de loin le plus gros gain de FPS possible.
    """
    oculus_path = os.path.join(CONFIG_DIR, "oculus.properties")
    try:
        lines = _read_properties_lines(oculus_path)
        _write_properties(oculus_path, lines, {"enableShaders": "true" if enabled else "false"})
    except Exception as e:
        print(f"set_shaders_enabled: {e}")


def ensure_default_shader(default_filename: str):
    """Active le shader par defaut du pack dans oculus.properties.

    Regle (respecte le choix du joueur) :
    - Si le shader actuellement selectionne existe toujours -> on ne touche a rien.
    - Si oculus.properties n'existe pas (1er lancement / nouvel ami) -> on cree le fichier
      avec le shader par defaut ET enableShaders=true.
    - Si le shader selectionne a ete supprime (ex: ancien Hysteria) -> on remplace par le
      shader par defaut, en conservant le reglage enableShaders existant (on/off).
    """
    default_filename = safe_filename(default_filename, "")
    if not default_filename:
        return
    try:
        oculus_path = os.path.join(CONFIG_DIR, "oculus.properties")
        lines = _read_properties_lines(oculus_path)

        current = None
        for line in lines:
            if line.startswith("shaderPack="):
                current = safe_filename(line.split("=", 1)[1].strip(), "")
                break

        # Le shader actuel est-il toujours present sur le disque ?
        if current and os.path.exists(os.path.join(SHADERPACKS_DIR, current)):
            return  # choix du joueur valide -> on n'y touche pas

        updates = {"shaderPack": default_filename}
        if not os.path.exists(oculus_path):
            updates["enableShaders"] = "true"  # nouvelle install : on active le shader de base

        _write_properties(oculus_path, lines, updates)
    except Exception as e:
        print(f"ensure_default_shader: {e}")


def sync_mods(on_progress=None, on_complete=None, on_overall=None):
    """Synchronise mods, resource packs et shaders.

    - on_progress(nom, statut) : statut par fichier ("downloading", "deleting", "ok"...)
    - on_overall(faits, total) : progression globale (pour une barre de progression)
    - on_complete(erreur)      : None si succès, sinon le message d'erreur

    Les téléchargements sont effectués en parallèle pour gagner du temps.
    """
    def _run():
        try:
            modpack = fetch_modpack()

            # (clé dans le modpack, dossier cible, extension par défaut)
            categories = [
                ("mods", MODS_DIR, ".jar"),
                ("resourcepacks", RESOURCEPACKS_DIR, ".zip"),
                ("shaderpacks", SHADERPACKS_DIR, ".zip"),
            ]

            tasks = []                 # (item, dossier, ext) à télécharger
            valid_by_dir = {}          # dossier -> set des fichiers attendus
            for key, target_dir, ext in categories:
                valid_by_dir.setdefault(target_dir, set())
                for item in modpack.get(key, []):
                    # Même nettoyage que dans download_file_item, sinon le ménage
                    # ci-dessous supprimerait le fichier qu'on vient de télécharger
                    raw_name = item.get("filename") or (str(item.get("name", "inconnu")) + ext)
                    valid_by_dir[target_dir].add(safe_filename(raw_name, "fichier_inconnu" + ext))
                    tasks.append((item, target_dir, ext))

            total = len(tasks)
            done = 0
            lock = threading.Lock()
            errors = []

            def worker(args):
                nonlocal done
                item, target_dir, ext = args
                try:
                    download_file_item(item, target_dir, ext, on_progress)
                except Exception as e:
                    with lock:
                        errors.append(str(e))
                finally:
                    with lock:
                        done += 1
                        if on_overall:
                            on_overall(done, total)

            # Téléchargements en parallèle (6 fichiers à la fois)
            if total:
                with ThreadPoolExecutor(max_workers=6) as executor:
                    list(executor.map(worker, tasks))

            if errors:
                if on_complete:
                    on_complete(errors[0])
                return

            # Nettoyage : on supprime les fichiers qui ne sont plus dans le modpack.json.
            # On ne touche qu'aux archives gérées par le launcher (.jar / .zip) : Oculus
            # enregistre les réglages d'un shader dans « shaderpacks/MonShader.zip.txt »,
            # et ce fichier doit survivre à la synchro (sinon le joueur perd ses options
            # de shader à chaque lancement).
            for key, target_dir, ext in categories:
                if not os.path.exists(target_dir):
                    continue
                valid = valid_by_dir[target_dir]
                # Réglages à conserver : « <archive gardée>.txt »
                keep_settings = {f"{name}.txt".lower() for name in valid}
                for file in os.listdir(target_dir):
                    filepath = os.path.join(target_dir, file)
                    if not os.path.isfile(filepath) or file in valid:
                        continue

                    lower = file.lower()
                    is_managed_archive = lower.endswith(ext)
                    # Réglages d'un shader qui n'est plus dans le modpack -> orphelin
                    is_orphan_settings = (
                        target_dir == SHADERPACKS_DIR
                        and lower.endswith(".txt")
                        and lower not in keep_settings
                    )
                    if not (is_managed_archive or is_orphan_settings):
                        continue  # fichier qui n'appartient pas au launcher : on le laisse

                    if on_progress:
                        on_progress(file, "deleting")
                    try:
                        os.remove(filepath)
                    except:
                        pass

            # Active le shader de base du pack (1er de la liste) si necessaire
            shaderpacks = modpack.get("shaderpacks", [])
            if shaderpacks:
                ensure_default_shader(shaderpacks[0].get("filename"))

            if on_complete:
                on_complete(None)
        except Exception as e:
            if on_complete:
                on_complete(str(e))

    threading.Thread(target=_run, daemon=True).start()