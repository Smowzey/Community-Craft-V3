import requests
import json
import mod_manager as mm

# La clé n'est plus écrite en dur ici : elle vient de la variable d'environnement
# CURSEFORGE_API_KEY ou d'un fichier curseforge_key.txt (voir mod_manager.py).
API_KEY = mm.CURSEFORGE_API_KEY

if API_KEY == "":
    print("❌ ERREUR : aucune clé d'API CurseForge trouvée (variable CURSEFORGE_API_KEY ou curseforge_key.txt) !")
    exit(1)

MC_VERSION = "1.20.1"

with open("modpack.json", "r", encoding="utf-8") as f:
    modpack = json.load(f)

headers = {"x-api-key": API_KEY}

print(f"Recherche des fichiers 1.20.1 pour {len(modpack['mods'])} mods...\n")

not_found = []

for mod in modpack["mods"]:
    # Skip les mods qui ont déjà un filename ET un file_id
    if mod.get("filename") and mod.get("curseforge_file_id", 0) != 0:
        print(f"⏭ {mod['name']} → déjà renseigné, on passe")
        continue

    project_id = mod.get("curseforge_project_id")
    if not project_id:
        print(f"⚠️ {mod['name']} → pas de project_id, on passe")
        not_found.append(mod["name"])
        continue

    r = requests.get(
        f"https://api.curseforge.com/v1/mods/{project_id}/files",
        headers=headers,
        params={"gameVersion": MC_VERSION, "modLoaderType": 1}
    )

    if r.status_code in (401, 403):
        print(f"\n❌ ERREUR d'API : Ta clé d'API est invalide ou refusée (Code {r.status_code}).")
        exit(1)
    elif r.status_code != 200:
        print(f"⚠️ Erreur {r.status_code} en cherchant le mod {mod['name']}")
        not_found.append(mod["name"])
        continue

    files = r.json().get("data", [])
    if files:
        best = files[0]
        mod["curseforge_file_id"] = best["id"]
        mod["filename"] = best["fileName"]
        print(f"✓ {mod['name']} → {best['fileName']} (ID: {best['id']})")
    else:
        not_found.append(mod["name"])
        print(f"✗ {mod['name']} → aucun fichier 1.20.1 Forge trouvé")

with open("modpack.json", "w", encoding="utf-8") as f:
    json.dump(modpack, f, indent=2, ensure_ascii=False)

print(f"\n✅ modpack.json mis à jour !")
if not_found:
    print(f"\n⚠️ Mods sans fichier 1.20.1 trouvé :")
    for name in not_found:
        print(f"  - {name}")