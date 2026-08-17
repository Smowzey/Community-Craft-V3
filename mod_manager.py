import os
import base64
import hashlib
import json
import requests
import threading
import urllib.parse
import zipfile
import time
from concurrent.futures import ThreadPoolExecutor

GITHUB_OWNER = "Smowzey"
GITHUB_REPO = "community-craft-v3"
GITHUB_BRANCH = "main"

MODPACK_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/modpack.json"

# GitHub limite le nombre de requêtes par IP sur raw.githubusercontent.com : quand
# plusieurs joueurs lancent le launcher en même temps (ou derrière le même réseau),
# on récupère un « 429 Too Many Requests » ou une connexion qui traîne jusqu'au
# timeout. On garde donc plusieurs sources pour le même fichier, essayées dans
# l'ordre, de la plus fraîche à la plus tolérante :
#   1. raw.githubusercontent.com  -> la source normale
#   2. api.github.com             -> même contenu, autre quota, toujours à jour
#   3. cdn.jsdelivr.net           -> CDN mondial, peut avoir quelques heures de retard
MODPACK_SOURCES = (
    MODPACK_URL,
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/modpack.json?ref={GITHUB_BRANCH}",
    f"https://cdn.jsdelivr.net/gh/{GITHUB_OWNER}/{GITHUB_REPO}@{GITHUB_BRANCH}/modpack.json",
)

# Sans User-Agent, GitHub prend le launcher pour un robot et répond 429 plus vite.
USER_AGENT = "CommunityCraft-Launcher/3.3 (+https://github.com/Smowzey/community-craft-v3)"
HTTP_HEADERS = {"User-Agent": USER_AGENT}

# (connexion, lecture) : on laisse le temps à GitHub de répondre quand il rame,
# au lieu d'abandonner au bout de 10 s.
HTTP_TIMEOUT = (10, 30)

GAME_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), ".community-craft-v3")
# Dernier modpack.json valide reçu : filet de sécurité si GitHub est injoignable
# au moment d'une ouverture de serveur.
MODPACK_CACHE = os.path.join(GAME_DIR, "modpack.cache.json")
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


def _parse_modpack_payload(response) -> dict:
    """Transforme une réponse en dictionnaire modpack.

    L'API GitHub ne renvoie pas le fichier tel quel mais une enveloppe JSON
    contenant le fichier encodé en base64 : on la déballe ici.
    """
    data = response.json()
    if not isinstance(data, dict):
        raise Exception("réponse inattendue (ce n'est pas un modpack)")
    if data.get("encoding") == "base64" and data.get("content"):
        data = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
    if not isinstance(data, dict) or "mods" not in data:
        raise Exception("modpack.json invalide (clé « mods » absente)")
    return data


def _save_modpack_cache(modpack: dict):
    try:
        os.makedirs(GAME_DIR, exist_ok=True)
        tmp = MODPACK_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(modpack, f)
        os.replace(tmp, MODPACK_CACHE)
    except Exception as e:
        print(f"cache modpack non écrit : {e}")


def _load_modpack_cache():
    try:
        if os.path.exists(MODPACK_CACHE):
            with open(MODPACK_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "mods" in data:
                return data
    except Exception as e:
        print(f"cache modpack illisible : {e}")
    return None


def fetch_modpack() -> dict:
    """Récupère le modpack.json, en résistant aux caprices de GitHub.

    On essaie chaque source à tour de rôle, deux fois, avec une pause qui
    s'allonge. En tout dernier recours on repart sur le dernier modpack.json
    valide déjà reçu : mieux vaut lancer le jeu avec la liste de la veille que
    de bloquer les joueurs devant une erreur réseau.
    """
    errors = []
    for attempt in range(2):
        for source in MODPACK_SOURCES:
            # Cache-buster : GitHub garde le fichier ~5 min en cache CDN. On l'arrondit
            # à la minute pour rester frais sans forcer un aller-retour complet à chaque
            # clic (ce qui accélère justement l'arrivée du 429).
            sep = "&" if "?" in source else "?"
            url = f"{source}{sep}t={int(time.time() // 60)}"
            headers = dict(HTTP_HEADERS)
            if "api.github.com" in source:
                headers["Accept"] = "application/vnd.github+json"
            try:
                response = requests.get(url, timeout=HTTP_TIMEOUT, headers=headers)
                if response.status_code == 200:
                    modpack = _parse_modpack_payload(response)
                    _save_modpack_cache(modpack)
                    return modpack
                if response.status_code == 404:
                    errors.append(f"{urllib.parse.urlparse(source).hostname} : introuvable (404)")
                elif response.status_code == 429:
                    errors.append(f"{urllib.parse.urlparse(source).hostname} : trop de requêtes (429)")
                else:
                    errors.append(f"{urllib.parse.urlparse(source).hostname} : HTTP {response.status_code}")
            except Exception as e:
                errors.append(f"{urllib.parse.urlparse(source).hostname} : {type(e).__name__}")
        if attempt == 0:
            time.sleep(3)  # laisse passer le pic avant de retenter

    cached = _load_modpack_cache()
    if cached is not None:
        print("modpack : GitHub injoignable, utilisation du cache local (" + " | ".join(errors) + ")")
        return cached

    raise Exception(
        "impossible de récupérer la liste des mods (GitHub injoignable). Détail : "
        + " | ".join(errors[-3:])
    )


def sha256_of_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_curseforge_download_url(project_id: int, file_id: int, filename: str) -> str:
    """Récupère l'URL de téléchargement depuis l'API CurseForge."""
    headers = {"x-api-key": CURSEFORGE_API_KEY, "User-Agent": USER_AGENT}
    url = f"https://api.curseforge.com/v1/mods/{project_id}/files/{file_id}/download-url"
    # Si l'API ne répond pas (timeout, quota, panne), ce n'est pas bloquant :
    # on sait reconstruire le lien CDN nous-mêmes juste en dessous.
    try:
        response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        if response.status_code == 200:
            data = response.json().get("data")
            if data:
                return data
    except Exception as e:
        print(f"API CurseForge indisponible pour {filename} ({type(e).__name__}), passage par le CDN")


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

    # On ajoute une "carte d'identité" (User-Agent) pour que Modrinth ne nous bloque pas
    headers = dict(HTTP_HEADERS)

    # Téléchargement avec jusqu'à 4 essais (réseau instable) + vérification d'intégrité
    last_error = None
    for attempt in range(1, 5):
        try:
            with requests.get(download_url, stream=True, timeout=(10, 60), headers=headers) as response:
                # Serveur saturé (429) ou en panne passagère (5xx) : ça vaut le coup
                # de réessayer, en respectant le délai demandé s'il y en a un.
                if response.status_code == 429 or response.status_code >= 500:
                    wait = 2.0 * attempt
                    try:
                        wait = max(wait, min(float(response.headers.get("Retry-After", 0)), 15))
                    except (TypeError, ValueError):
                        pass
                    raise Exception(f"serveur occupé (HTTP {response.status_code}), nouvelle tentative dans {wait:.0f}s")
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
            if attempt < 4:
                time.sleep(2.0 * attempt)  # pause croissante avant de réessayer

    raise Exception(f"Échec du téléchargement de {filename} après 4 essais : {last_error}")


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