import base64
import ctypes
import json
import os
import re
import socket
import threading
import time
import uuid
import webbrowser
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from minecraft_launcher_lib import microsoft_account
from minecraft_launcher_lib.exceptions import AccountNotOwnMinecraft, InvalidRefreshToken

GAME_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), ".community-craft-v3")
TOKEN_FILE = os.path.join(GAME_DIR, "session.json")

# --- Connexion Microsoft ----------------------------------------------------
# L'ID d'application Azure n'est pas un secret : pour une application « client
# public » (bureau), il est prévu pour être embarqué. Il n'y a aucun client
# secret ici, c'est le flux PKCE qui protège l'échange.
MICROSOFT_CLIENT_ID = "ac783206-67f2-407b-862d-6b0418dea1c2"

# Port d'écoute local pour récupérer la redirection OAuth.
# À déclarer dans Azure (Applications mobiles et de bureau) : http://localhost:7899
REDIRECT_PORT = 7899
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"

SESSION_OFFLINE = "offline"
SESSION_MICROSOFT = "microsoft"

# Un pseudo Minecraft valide : 3 à 16 caractères, lettres/chiffres/underscore.
# On l'impose parce que le pseudo finit dans la ligne de commande Java : sans
# contrôle, un pseudo du genre « toto --gameDir C:\ » injecterait des arguments.
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
# Microsoft renvoie l'UUID sans tirets, le mode hors-ligne avec : on accepte les deux
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")


def validate_username(username: str) -> str:
    """Vérifie et normalise un pseudo. Lève ValueError si invalide."""
    username = (username or "").strip()
    if not username:
        raise ValueError("Le pseudo ne peut pas être vide.")
    if not USERNAME_RE.match(username):
        raise ValueError(
            "Pseudo invalide : 3 à 16 caractères, uniquement des lettres, "
            "des chiffres et des underscores (_)."
        )
    return username


# --- Chiffrement des jetons (DPAPI Windows) ---------------------------------
# Un refresh token Microsoft est réutilisable pendant des semaines : le laisser
# en clair dans session.json, c'est laisser n'importe quel programme du PC
# emprunter le compte. DPAPI le chiffre avec la session Windows de l'utilisateur.

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_to_bytes(blob) -> bytes:
    data = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)
    return data


def _dpapi(func_name, data: bytes) -> bytes | None:
    """Appelle CryptProtectData / CryptUnprotectData. None si indisponible."""
    if os.name != "nt":
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        func = getattr(crypt32, func_name)
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()
        # CRYPTPROTECT_UI_FORBIDDEN = 0x1 : jamais de fenêtre système
        ok = func(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out))
        if not ok:
            return None
        return _blob_to_bytes(blob_out)
    except Exception as e:
        print(f"DPAPI ({func_name}) indisponible : {e}")
        return None


def _encrypt_secrets(secrets: dict) -> tuple[str, bool]:
    """Renvoie (valeur stockée, chiffré ?)."""
    raw = json.dumps(secrets).encode("utf-8")
    protected = _dpapi("CryptProtectData", raw)
    if protected is None:
        # Pas de DPAPI : on stocke quand même, mais en clair (et on le signale)
        return base64.b64encode(raw).decode("ascii"), False
    return base64.b64encode(protected).decode("ascii"), True


def _decrypt_secrets(stored: str, encrypted: bool) -> dict:
    try:
        raw = base64.b64decode(stored)
    except Exception:
        return {}
    if encrypted:
        raw = _dpapi("CryptUnprotectData", raw)
        if raw is None:
            return {}
    try:
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# --- Stockage de la session -------------------------------------------------
def save_session(data: dict):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    # Écriture atomique : pas de session.json tronqué si le PC s'éteint en plein write
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, TOKEN_FILE)


def load_session() -> dict | None:
    """Recharge la session locale, en revalidant son contenu.

    Le fichier est modifiable à la main : on ne fait pas confiance à ce qu'il
    contient, on revérifie le pseudo et l'UUID avant de les réutiliser.
    """
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    try:
        username = validate_username(data.get("username", ""))
    except ValueError:
        return None

    session_type = data.get("type", SESSION_OFFLINE)
    session_uuid = str(data.get("uuid", ""))
    if not UUID_RE.match(session_uuid):
        session_uuid = offline_uuid(username)

    if session_type != SESSION_MICROSOFT:
        return {
            "type": SESSION_OFFLINE,
            "access_token": "0",
            "username": username,
            "uuid": session_uuid,
        }

    secrets = _decrypt_secrets(data.get("secrets", ""), bool(data.get("encrypted")))
    if not secrets.get("refresh_token"):
        return None  # session Microsoft inutilisable : on repart sur une connexion
    return {
        "type": SESSION_MICROSOFT,
        "access_token": secrets.get("access_token", ""),
        "refresh_token": secrets["refresh_token"],
        "username": username,
        "uuid": session_uuid,
        "saved_at": data.get("saved_at", 0),
    }


def clear_session():
    """Déconnexion : on efface la session enregistrée."""
    try:
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
    except OSError as e:
        print(f"Impossible d'effacer la session : {e}")


# --- Mode hors-ligne (crack) — inchangé -------------------------------------
def offline_uuid(username: str) -> str:
    """UUID hors-ligne standard : le joueur conserve son stuff entre les sessions."""
    return str(uuid.uuid3(uuid.NAMESPACE_DNS, f"OfflinePlayer:{username}"))


def login_offline(username: str) -> dict:
    """Crée une session hors-ligne (crack) basée sur le pseudo."""
    username = validate_username(username)

    session = {
        "type": SESSION_OFFLINE,
        "access_token": "0",  # Pas de vrai token en mode crack
        "username": username,
        "uuid": offline_uuid(username),
    }
    save_session(session)
    return session


# --- Mode Microsoft ---------------------------------------------------------
class _RedirectHandler(BaseHTTPRequestHandler):
    """Reçoit la redirection OAuth du navigateur sur 127.0.0.1."""

    captured_url = None
    done_event = None

    def do_GET(self):
        _RedirectHandler.captured_url = self.path
        ok = "code=" in self.path
        body = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Community Craft</title></head>
<body style="background:#0B0B0E;color:#F1F1F4;font-family:Segoe UI,sans-serif;
text-align:center;padding-top:80px">
<h2 style="color:{'#3ECFC4' if ok else '#E5484D'}">
{'Connexion reussie !' if ok else 'Connexion annulee'}</h2>
<p style="color:#8A8A95">Tu peux fermer cet onglet et revenir au launcher.</p>
</body></html>"""
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        if _RedirectHandler.done_event is not None:
            _RedirectHandler.done_event.set()

    def log_message(self, *args):
        pass  # pas de bruit dans la console


class _LoopbackServer(HTTPServer):
    # Sous Windows, SO_REUSEADDR (actif par défaut dans socketserver) laisse
    # deux sockets se lier au même port : le bind « réussirait » sur un port
    # déjà pris et on attendrait une redirection qui n'arriverait jamais.
    allow_reuse_address = False


def _wait_for_redirect(timeout: int, cancel_event: threading.Event | None):
    """Démarre le serveur local et attend le retour du navigateur."""
    done = threading.Event()
    _RedirectHandler.captured_url = None
    _RedirectHandler.done_event = done

    try:
        # 127.0.0.1 uniquement : rien n'écoute sur le réseau
        server = _LoopbackServer(("127.0.0.1", REDIRECT_PORT), _RedirectHandler)
    except OSError as e:
        raise RuntimeError(
            f"Impossible d'écouter sur le port {REDIRECT_PORT} : un autre programme "
            f"l'utilise déjà ({e}). Ferme-le puis réessaie."
        )

    server.timeout = 1
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True)
    thread.start()

    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if done.wait(0.3):
                return _RedirectHandler.captured_url
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Connexion annulée.")
        raise RuntimeError("Délai dépassé : la connexion Microsoft n'a pas abouti.")
    finally:
        server.shutdown()
        server.server_close()
        _RedirectHandler.done_event = None


def login_microsoft(on_status=None, cancel_event=None, timeout=300) -> dict:
    """Connexion à un vrai compte Minecraft via Microsoft (flux PKCE).

    Ouvre le navigateur : c'est le joueur qui saisit ses identifiants chez
    Microsoft, le launcher ne voit jamais son mot de passe.
    """
    def status(text):
        if on_status:
            on_status(text)

    status("Ouverture du navigateur…")
    login_url, state, code_verifier = microsoft_account.get_secure_login_data(
        MICROSOFT_CLIENT_ID, REDIRECT_URI)

    webbrowser.open(login_url)
    status("En attente de la connexion dans le navigateur…")
    redirect_url = _wait_for_redirect(timeout, cancel_event)

    full_url = f"{REDIRECT_URI}{redirect_url}"
    if "error=" in (redirect_url or ""):
        raise RuntimeError("Connexion refusée ou annulée dans le navigateur.")
    if not microsoft_account.url_contains_auth_code(full_url):
        raise RuntimeError("Réponse Microsoft inattendue (pas de code d'autorisation).")

    status("Validation du compte Minecraft…")
    # parse_auth_code_url vérifie le paramètre state (protection CSRF)
    auth_code = microsoft_account.parse_auth_code_url(full_url, state)
    token_request = microsoft_account.get_authorization_token(
        MICROSOFT_CLIENT_ID, None, REDIRECT_URI, auth_code, code_verifier)
    login_data = _finish_login(token_request, status)
    return _store_microsoft_session(login_data)


def refresh_microsoft(session: dict) -> dict:
    """Renouvelle le jeton d'accès à partir du refresh token enregistré."""
    refresh_token = session.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Aucun jeton de rafraîchissement : reconnecte-toi.")
    try:
        token_request = microsoft_account.refresh_authorization_token(
            MICROSOFT_CLIENT_ID, None, REDIRECT_URI, refresh_token)
    except InvalidRefreshToken:
        raise RuntimeError("Session Microsoft expirée : reconnecte-toi.")
    if "access_token" not in token_request:
        raise RuntimeError("Session Microsoft expirée : reconnecte-toi.")
    return _store_microsoft_session(_finish_login(token_request))


# Codes d'erreur XSTS renvoyés par Xbox Live
XSTS_ERRORS = {
    "2148916233": "Ce compte Microsoft n'a pas de compte Xbox. Crées-en un sur xbox.com puis réessaie.",
    "2148916235": "Xbox Live n'est pas disponible dans le pays de ce compte.",
    "2148916236": "Ce compte nécessite une vérification d'adulte.",
    "2148916237": "Ce compte nécessite une vérification d'adulte.",
    "2148916238": "Compte enfant : il doit être rattaché à une famille Microsoft pour utiliser Xbox Live.",
}


def _finish_login(token_request, status=None):
    """Xbox Live → XSTS → Minecraft, en remontant les vraies erreurs HTTP.

    complete_login() de la bibliothèque avale la réponse du serveur et lève un
    AzureAppNotPermitted sans détail : on refait la chaîne nous-mêmes pour
    pouvoir dire précisément ce qui coince.
    """
    def say(text):
        if status:
            status(text)

    say("Authentification Xbox Live…")
    xbl_request = microsoft_account.authenticate_with_xbl(token_request["access_token"])
    try:
        xbl_token = xbl_request["Token"]
        userhash = xbl_request["DisplayClaims"]["xui"][0]["uhs"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"Réponse Xbox Live inattendue : {str(xbl_request)[:200]}")

    say("Vérification XSTS…")
    xsts_request = microsoft_account.authenticate_with_xsts(xbl_token)
    if "Token" not in xsts_request:
        xerr = str(xsts_request.get("XErr", ""))
        if xerr in XSTS_ERRORS:
            raise RuntimeError(XSTS_ERRORS[xerr])
        raise RuntimeError(f"Xbox Live a refusé la connexion : {str(xsts_request)[:200]}")
    xsts_token = xsts_request["Token"]

    say("Connexion aux services Minecraft…")
    response = requests.post(
        "https://api.minecraftservices.com/authentication/login_with_xbox",
        json={"identityToken": f"XBL3.0 x={userhash};{xsts_token}"},
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "user-agent": microsoft_account.get_user_agent()},
        timeout=30,
    )
    try:
        account = response.json()
    except ValueError:
        account = {}

    if "access_token" not in account:
        body = (response.text or "").strip()[:300]
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"Refus des services Minecraft (HTTP {response.status_code}). "
                "L'ID d'application Azure n'est pas encore autorisé à utiliser l'API Minecraft : "
                "la configuration Azure ne suffit pas, il faut faire approuver l'ID "
                "via le formulaire Microsoft « review app id » (aka.ms/mce-reviewappid). "
                f"Réponse : {body or '(vide)'}"
            )
        raise RuntimeError(
            f"Les services Minecraft ont répondu HTTP {response.status_code} : {body or '(vide)'}")

    access_token = account["access_token"]

    say("Récupération du profil…")
    try:
        profile = microsoft_account.get_profile(access_token)
    except AccountNotOwnMinecraft:
        raise RuntimeError("Ce compte Microsoft ne possède pas Minecraft Java Edition.")
    if "id" not in profile or "name" not in profile:
        raise RuntimeError(
            "Ce compte Microsoft ne possède pas de profil Minecraft Java Edition "
            f"({str(profile)[:150]})."
        )

    return {
        "id": profile["id"],
        "name": profile["name"],
        "access_token": access_token,
        "refresh_token": token_request.get("refresh_token", ""),
    }


def _store_microsoft_session(login_data) -> dict:
    """Valide la réponse Microsoft puis enregistre la session (jetons chiffrés)."""
    username = str(login_data.get("name", ""))
    try:
        username = validate_username(username)
    except ValueError:
        raise RuntimeError(f"Pseudo Minecraft inattendu renvoyé par Microsoft : {username!r}")

    account_uuid = str(login_data.get("id", ""))
    if not UUID_RE.match(account_uuid):
        raise RuntimeError("UUID de compte invalide renvoyé par Microsoft.")

    access_token = str(login_data.get("access_token", ""))
    refresh_token = str(login_data.get("refresh_token", ""))
    if not access_token:
        raise RuntimeError("Microsoft n'a pas renvoyé de jeton d'accès.")

    stored, encrypted = _encrypt_secrets(
        {"access_token": access_token, "refresh_token": refresh_token})
    save_session({
        "type": SESSION_MICROSOFT,
        "username": username,
        "uuid": account_uuid,
        "secrets": stored,
        "encrypted": encrypted,
        "saved_at": int(time.time()),
    })

    return {
        "type": SESSION_MICROSOFT,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "username": username,
        "uuid": account_uuid,
    }
