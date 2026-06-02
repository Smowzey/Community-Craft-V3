import json
import os
import uuid

TOKEN_FILE = os.path.join(os.getenv("APPDATA"), ".community-craft-v3", "session.json")


def save_session(data: dict):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)


def load_session() -> dict | None:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None


def login_offline(username: str) -> dict:
    """Crée une session hors-ligne (crack) basée sur le pseudo."""
    if not username or not username.strip():
        raise ValueError("Le pseudo ne peut pas être vide.")
        
    username = username.strip()
    # Génère un UUID offline standard pour que le joueur conserve son stuff
    offline_uuid = str(uuid.uuid3(uuid.NAMESPACE_DNS, f"OfflinePlayer:{username}"))
    
    session = {
        "access_token": "0", # Pas de vrai token en mode crack
        "username": username,
        "uuid": offline_uuid,
    }
    save_session(session)
    return session