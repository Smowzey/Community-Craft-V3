"""Prépare le dossier mods du SERVEUR à partir de modpack.json.

Télécharge tous les mods SAUF ceux qui sont client-only (shaders, minimap,
interface...) car certains font planter un serveur dédié.

Résultat : un dossier ./server/mods/ prêt à copier sur ton serveur Forge.

Usage : python server_setup.py
"""
import os
import json
import mod_manager as mm

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_MODS = os.path.join(HERE, "server", "mods")

# Mods CLIENT-ONLY : à NE PAS installer sur le serveur.
# (Embeddium, Oculus et EnhancedVisuals font carrément planter un serveur dédié.)
CLIENT_ONLY = {
    "Xaero's Minimap",
    "Xaero's World Map",
    "Xaero's Minimap & World Map - Waystones Compatibility",
    "Controlling",
    "Searchables",            # uniquement requis par Controlling (client)
    "EnhancedVisuals",
    "Just Enough Items",
    "Embeddium",
    "Oculus",
    "Skin Restorer",
    "Ambient Environment",
    "BadOptimizations",
}


def main():
    os.makedirs(SERVER_MODS, exist_ok=True)
    with open(os.path.join(HERE, "modpack.json"), "r", encoding="utf-8") as f:
        data = json.load(f)

    downloaded = skipped = errors = 0
    for mod in data.get("mods", []):
        name = mod.get("name", "?")
        if name in CLIENT_ONLY:
            print(f"SKIP (client-only)  {name}")
            skipped += 1
            continue
        try:
            mm.download_file_item(mod, SERVER_MODS, ".jar")
            print(f"OK                  {name}")
            downloaded += 1
        except Exception as e:
            print(f"ERREUR              {name} : {e}")
            errors += 1

    print(f"\nTermine : {downloaded} mods serveur, {skipped} client-only ignores, {errors} erreurs.")
    print(f"Dossier pret a copier : {SERVER_MODS}")
    if errors:
        print("Relance le script pour réessayer les téléchargements en erreur.")


if __name__ == "__main__":
    main()
