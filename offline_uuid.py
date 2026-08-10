"""Calcule l'UUID hors-ligne d'un pseudo, tel que le serveur Minecraft le génère.

En `online-mode=false`, le serveur identifie un joueur par un UUID derive du pseudo
(MD5 de "OfflinePlayer:<pseudo>"). La commande /whitelist add, elle, va chercher
l'UUID du vrai compte chez Mojang : les deux ne correspondent pas et le joueur est
refuse. Ce script sort l'entree exacte a coller dans whitelist.json / ops.json.

Usage : python offline_uuid.py <pseudo> [<pseudo2> ...]
"""
import sys
import json
import uuid
import hashlib


def offline_uuid(username: str) -> str:
    """Reproduit UUID.nameUUIDFromBytes de Minecraft : MD5 brut, sans namespace."""
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{username}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30  # version 3
    digest[8] = (digest[8] & 0x3F) | 0x80  # variant RFC 4122
    return str(uuid.UUID(bytes=bytes(digest)))


def main():
    pseudos = sys.argv[1:]
    if not pseudos:
        print(__doc__)
        return 1

    whitelist = [{"uuid": offline_uuid(p), "name": p} for p in pseudos]
    ops = [{"uuid": offline_uuid(p), "name": p, "level": 4, "bypassesPlayerLimit": False}
           for p in pseudos]

    for entry in whitelist:
        print(f"{entry['name']:<20} {entry['uuid']}")

    print("\n--- whitelist.json ---")
    print(json.dumps(whitelist, indent=2))
    print("\n--- ops.json (niveau 4 = admin) ---")
    print(json.dumps(ops, indent=2))
    print("\nApres modification : redemarre le serveur, ou tape /whitelist reload en console.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
