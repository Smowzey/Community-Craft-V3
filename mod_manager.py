import os
import hashlib
import requests
import threading
import urllib.parse
import zipfile
import time
from concurrent.futures import ThreadPoolExecutor

MODPACK_URL = "https://raw.githubusercontent.com/Smowzey/community-craft-v3/main/modpack.json"
MODS_DIR = os.path.join(os.getenv("APPDATA"), ".community-craft-v3", "mods")
RESOURCEPACKS_DIR = os.path.join(os.getenv("APPDATA"), ".community-craft-v3", "resourcepacks")
SHADERPACKS_DIR = os.path.join(os.getenv("APPDATA"), ".community-craft-v3", "shaderpacks")
CURSEFORGE_API_KEY = "$2a$10$ogJO1kKcvpUth60qurFiaeaJ8vjDyk3Z0v2W54oXt/cbyi2gbpSvy"  # → console.curseforge.com


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
    file_id_str = str(file_id)
    # Découpage correct pour les ID à 6 ou 7 chiffres (ex: 1234567 -> 1234 / 567)
    part1 = file_id_str[:-3]
    part2 = file_id_str[-3:]
    return f"https://edge.forgecdn.net/files/{part1}/{part2}/{urllib.parse.quote(filename)}"


def download_file_item(item: dict, target_dir: str, default_ext: str, on_progress=None):
    os.makedirs(target_dir, exist_ok=True)
    
    # On s'assure d'avoir au moins un nom de fichier, sinon on utilise le nom par défaut
    filename = item.get("filename", item.get("name", "fichier_inconnu") + default_ext)
    filepath = os.path.join(target_dir, filename)

    # Déjà installé et à jour ?
    if os.path.exists(filepath):
        # Vérifie que le fichier n'est pas corrompu (un vrai fichier JAR/ZIP)
        if zipfile.is_zipfile(filepath):
            expected_sha = item.get("sha256", "")
            if not expected_sha or sha256_of_file(filepath) == expected_sha:
                if on_progress:
                    on_progress(item.get("name", filename), "ok")
                return
        else:
            os.remove(filepath)  # Fichier corrompu, on le supprime pour le retélécharger

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

    try:
        # On ajoute une fausse "carte d'identité" (User-Agent) pour que Modrinth ne nous bloque pas
        headers = {"User-Agent": "CommunityCraft-Launcher/3.0"}
        response = requests.get(download_url, stream=True, timeout=60, headers=headers)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise Exception(f"Erreur HTTP: Le téléchargement de {filename} a échoué ({e}).")

    with open(filepath, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    if on_progress:
        on_progress(item.get("name", filename), "done")


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
                    filename = item.get("filename", item.get("name", "inconnu") + ext)
                    valid_by_dir[target_dir].add(filename)
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

            # Nettoyage : on supprime les fichiers qui ne sont plus dans le modpack.json
            for key, target_dir, ext in categories:
                if os.path.exists(target_dir):
                    for file in os.listdir(target_dir):
                        filepath = os.path.join(target_dir, file)
                        if os.path.isfile(filepath) and file not in valid_by_dir[target_dir]:
                            if on_progress:
                                on_progress(file, "deleting")
                            try:
                                os.remove(filepath)
                            except:
                                pass

            if on_complete:
                on_complete(None)
        except Exception as e:
            if on_complete:
                on_complete(str(e))

    threading.Thread(target=_run, daemon=True).start()