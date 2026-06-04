"""Remplit le champ sha256 de chaque entrée de modpack.json.

Pour chaque mod / resourcepack / shaderpack :
- si le fichier est déjà présent en local et valide -> on calcule son empreinte ;
- sinon on le télécharge (URL directe ou API CurseForge) pour calculer l'empreinte.

À relancer après chaque changement de version d'un mod dans modpack.json.
Usage : python fill_hashes.py
"""
import os
import json
import zipfile
import tempfile
import requests
import mod_manager as mm

MODPACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modpack.json")
HEADERS = {"User-Agent": "CommunityCraft-Launcher/3.0"}

CATEGORIES = [
    ("mods", mm.MODS_DIR, ".jar"),
    ("resourcepacks", mm.RESOURCEPACKS_DIR, ".zip"),
    ("shaderpacks", mm.SHADERPACKS_DIR, ".zip"),
]


def resolve_url(item, filename):
    url = item.get("url")
    if url:
        return url
    if "curseforge_project_id" in item and "curseforge_file_id" in item:
        return mm.get_curseforge_download_url(
            item["curseforge_project_id"], item["curseforge_file_id"], filename
        )
    return None


def compute_sha(item, target_dir, ext):
    filename = item.get("filename", item.get("name", "x") + ext)
    local = os.path.join(target_dir, filename)
    # 1) Fichier local valide
    if os.path.exists(local) and zipfile.is_zipfile(local):
        return mm.sha256_of_file(local), "local"
    # 2) Téléchargement temporaire
    url = resolve_url(item, filename)
    if not url:
        return None, "pas-d-url"
    tmp = os.path.join(tempfile.gettempdir(), "ccv3_" + filename)
    with requests.get(url, stream=True, timeout=120, headers=HEADERS) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    try:
        if not zipfile.is_zipfile(tmp):
            return None, "telechargement-corrompu"
        return mm.sha256_of_file(tmp), "telecharge"
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main():
    with open(MODPACK, "r", encoding="utf-8") as f:
        data = json.load(f)

    done = skipped = 0
    for key, target_dir, ext in CATEGORIES:
        for item in data.get(key, []):
            name = item.get("name", "?")
            try:
                sha, src = compute_sha(item, target_dir, ext)
                if sha:
                    item["sha256"] = sha
                    done += 1
                    print(f"OK   [{src:12}] {name}")
                else:
                    skipped += 1
                    print(f"SKIP [{src:12}] {name}")
            except Exception as e:
                skipped += 1
                print(f"ERR  {name}: {e}")

    with open(MODPACK, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nTermine : {done} empreintes remplies, {skipped} ignorees. modpack.json mis a jour.")


if __name__ == "__main__":
    main()
