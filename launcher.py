import customtkinter as ctk
import subprocess
import threading
import os
import json
import ctypes
import sys
import re
import time
import shutil
import webbrowser
import requests
from tkinter import messagebox
from PIL import Image
import minecraft_launcher_lib
from auth import load_session, login_offline
from mod_manager import sync_mods, fetch_modpack

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SERVER_NAME = "Community Craft V3"
MC_VERSION = "1.20.1"
FORGE_VERSION = "47.3.0"
PACK_VERSION = "v3.1"

# Version du launcher (utilisée pour l'auto-update)
LAUNCHER_VERSION = "3.1.0"

# Sources distantes (même dépôt GitHub que le modpack)
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/Smowzey/community-craft-v3/main"
NEWS_URL = f"{GITHUB_RAW_BASE}/news.json"
UPDATE_URL = f"{GITHUB_RAW_BASE}/launcher_version.json"

# Discord — à personnaliser
DISCORD_INVITE = "https://discord.gg/your-invite"   # ← remplace par ton vrai lien d'invitation
DISCORD_CLIENT_ID = ""  # ← optionnel : ID d'application Discord pour activer le Rich Presence

# Actualités par défaut si le fichier distant est inaccessible
DEFAULT_NEWS = (
    "• Refonte complète du Launcher.\n"
    "• Téléchargement automatique des shaders et packs de textures.\n"
    "• Optimisation massive des performances en jeu.\n\n"
    "Bon jeu sur le serveur !"
)

# Flags d'optimisation "Aikar" pour le Minecraft moddé (réduction des freezes / GC)
AIKARS_FLAGS = [
    "-XX:+UseG1GC",
    "-XX:+ParallelRefProcEnabled",
    "-XX:MaxGCPauseMillis=200",
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+DisableExplicitGC",
    "-XX:+AlwaysPreTouch",
    "-XX:G1NewSizePercent=30",
    "-XX:G1MaxNewSizePercent=40",
    "-XX:G1HeapRegionSize=8M",
    "-XX:G1ReservePercent=20",
    "-XX:G1HeapWastePercent=5",
    "-XX:G1MixedGCCountTarget=4",
    "-XX:InitiatingHeapOccupancyPercent=15",
    "-XX:G1MixedGCLiveThresholdPercent=90",
    "-XX:G1RSetUpdatingPauseTimePercent=5",
    "-XX:SurvivorRatio=32",
    "-XX:+PerfDisableSharedMem",
    "-XX:MaxTenuringThreshold=1",
]


def resource_path(relative_path):
    """Obtient le chemin absolu de la ressource, compatible avec PyInstaller"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class Sidebar(ctk.CTkFrame):
    def __init__(self, parent, on_navigate):
        super().__init__(parent, width=180, corner_radius=0)
        self.on_navigate = on_navigate
        self._build()

    def _build(self):
        self.grid_propagate(False)

        logo_frame = ctk.CTkFrame(self, fg_color="transparent")
        logo_frame.pack(pady=(20, 16), padx=16, fill="x")

        try:
            logo_image = ctk.CTkImage(Image.open(resource_path("logo.png")), size=(64, 64))
            ctk.CTkLabel(logo_frame, image=logo_image, text="").pack()
        except FileNotFoundError:
            ctk.CTkLabel(logo_frame, text="⬡", font=("Arial", 32), text_color="#00BFFF").pack()

        ctk.CTkLabel(logo_frame, text="Community Craft", font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(8, 0))
        ctk.CTkLabel(logo_frame, text="V3 — 2025", font=ctk.CTkFont(size=11), text_color="gray").pack()

        ctk.CTkFrame(self, height=1, fg_color="gray30").pack(fill="x", padx=16, pady=4)

        pages = [("Accueil", "home"), ("Mods", "mods"), ("Paramètres", "settings")]
        for label, key in pages:
            ctk.CTkButton(
                self, text=label, anchor="w",
                fg_color="transparent", hover_color="#2B2B2B",
                command=lambda k=key: self.on_navigate(k)
            ).pack(fill="x", padx=12, pady=2)

        self.user_frame = ctk.CTkFrame(self, fg_color="#141414", corner_radius=8)
        self.user_frame.pack(side="bottom", fill="x", padx=12, pady=16)

        self.username_label = ctk.CTkLabel(
            self.user_frame, text="Non connecté",
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.username_label.pack(pady=(8, 0))

        self.status_dot = ctk.CTkLabel(
            self.user_frame, text="● Hors ligne",
            font=ctk.CTkFont(size=11), text_color="gray"
        )
        self.status_dot.pack(pady=(0, 8))

    def set_user(self, username: str):
        self.username_label.configure(text=username)
        self.status_dot.configure(text="● Connecté", text_color="#00BFFF")


class HomePage(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._build()
        self._fetch_stats()

    def _build(self):
        server_card = ctk.CTkFrame(self, corner_radius=12)
        server_card.pack(fill="x", padx=20, pady=(20, 10))
        ctk.CTkLabel(server_card, text=SERVER_NAME,
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=16, pady=(12, 2))
                     
        tags_frame = ctk.CTkFrame(server_card, fg_color="transparent")
        tags_frame.pack(anchor="w", padx=16, pady=(0, 12))
        
        self.tag_mod_count = ctk.CTkLabel(tags_frame, text="Chargement...", fg_color="#152433", text_color="#00BFFF", corner_radius=6, font=ctk.CTkFont(size=11), padx=8, pady=2)
        self.tag_mod_count.pack(side="left", padx=4)
        
        for tag in [f"Forge {FORGE_VERSION}", f"MC {MC_VERSION}"]:
            ctk.CTkLabel(tags_frame, text=tag, fg_color="#152433", text_color="#00BFFF", corner_radius=6, font=ctk.CTkFont(size=11), padx=8, pady=2).pack(side="left", padx=4)

        stats_frame = ctk.CTkFrame(self, fg_color="transparent")
        stats_frame.pack(fill="x", padx=20, pady=4)
        
        self.stats_labels = {}
        ram_val = f"{self.app.settings.get('ram', 6)} Go"
        for val, label in [("...", "Mods"), (ram_val, "RAM"), (PACK_VERSION, "Version")]:
            card = ctk.CTkFrame(stats_frame, corner_radius=8)
            card.pack(side="left", expand=True, fill="x", padx=4)
            lbl_val = ctk.CTkLabel(card, text=val, font=ctk.CTkFont(size=20, weight="bold"))
            lbl_val.pack(pady=(10, 0))
            self.stats_labels[label] = lbl_val
            ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(0, 10))

        news_card = ctk.CTkFrame(self, corner_radius=12)
        news_card.pack(fill="both", expand=True, padx=20, pady=10)
        ctk.CTkLabel(news_card, text="📝 Actualités & Mises à jour", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16, pady=(12, 4))

        self.news_label = ctk.CTkLabel(news_card, text="Chargement des actualités...", font=ctk.CTkFont(size=13), text_color="gray", justify="left", anchor="w")
        self.news_label.pack(fill="both", padx=16, pady=(4, 12))
        self._fetch_news()

        self.status_label = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11), text_color="gray")
        self.status_label.pack(padx=20, pady=(0, 4))

        # Barre de progression (cachée par défaut, affichée pendant les installs/téléchargements)
        self.progress_bar = ctk.CTkProgressBar(self)
        self.progress_bar.set(0)

        self.login_btn = ctk.CTkButton(
            self, text="Choisir un pseudo",
            font=ctk.CTkFont(size=13), height=36, corner_radius=8,
            fg_color="#0055A4", hover_color="#003D7A",
            command=self.app._connect_minecraft
        )
        self.login_btn.pack(fill="x", padx=20, pady=(0, 6))

        self.play_btn = ctk.CTkButton(
            self, text="Jouer (Connexion requise)",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=48, corner_radius=10,
            fg_color="gray25", hover_color="gray30",
            text_color="gray50",
            state="disabled",
            command=self.app._launch_game
        )
        self.play_btn.pack(fill="x", padx=20, pady=(0, 8))

        # Boutons utilitaires : dossier du jeu, log, Discord
        util_frame = ctk.CTkFrame(self, fg_color="transparent")
        util_frame.pack(fill="x", padx=20, pady=(0, 16))
        for label, cmd in [
            ("📁 Dossier du jeu", self.app.open_game_folder),
            ("📄 Voir le log", self.app.open_log),
            ("💬 Discord", self.app.open_discord),
        ]:
            ctk.CTkButton(
                util_frame, text=label, height=28,
                fg_color="#2B2B2B", hover_color="#383838",
                font=ctk.CTkFont(size=12),
                command=cmd
            ).pack(side="left", expand=True, fill="x", padx=2)

    def show_progress(self):
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=20, pady=(0, 8), before=self.login_btn)

    def hide_progress(self):
        self.progress_bar.pack_forget()

    def set_progress(self, fraction: float):
        self.progress_bar.set(max(0.0, min(1.0, fraction)))

    def _fetch_news(self):
        def fetch():
            try:
                resp = requests.get(f"{NEWS_URL}?t={int(time.time())}", timeout=8)
                if resp.status_code != 200:
                    return
                data = resp.json()
                if isinstance(data, dict):
                    if data.get("content"):
                        text = data["content"]
                    elif data.get("items"):
                        text = "\n".join(f"• {i}" for i in data["items"])
                    else:
                        return
                elif isinstance(data, list):
                    text = "\n".join(f"• {i}" for i in data)
                else:
                    return
                self.app.after(0, lambda: self.news_label.configure(text=text))
            except Exception:
                # En cas d'échec, on retombe sur les actualités par défaut
                self.app.after(0, lambda: self.news_label.configure(text=DEFAULT_NEWS))
        # Affiche d'abord le texte par défaut, puis tente la mise à jour distante
        self.news_label.configure(text=DEFAULT_NEWS)
        threading.Thread(target=fetch, daemon=True).start()

    def _fetch_stats(self):
        def fetch():
            try:
                modpack = fetch_modpack()
                mod_count = len(modpack.get("mods", []))
                self.app.after(0, lambda: self._update_stats(mod_count))
            except:
                self.app.after(0, lambda: self._update_stats("?"))
        threading.Thread(target=fetch, daemon=True).start()
        
    def _update_stats(self, count):
        self.tag_mod_count.configure(text=f"{count} mods")
        if "Mods" in self.stats_labels:
            self.stats_labels["Mods"].configure(text=str(count))

    def set_connected(self, username: str):
        self.login_btn.configure(
            text=f"👤 Connecté : {username} (Changer de pseudo)",
            state="normal",
            fg_color="#2B2B2B",
            hover_color="#383838"
        )
        self.play_btn.configure(
            state="normal", 
            text="Jouer",
            fg_color="#1E90FF", 
            hover_color="#104E8B",
            text_color="white"
        )

    def set_status(self, text: str, color: str = "gray"):
        self.status_label.configure(text=text, text_color=color)


class ModsPage(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._build()
        self._load_mods()

    def _build(self):
        title = ctk.CTkLabel(self, text="📦 Liste des Mods", font=ctk.CTkFont(size=20, weight="bold"))
        title.pack(anchor="w", padx=20, pady=(20, 10))

        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self.loading_label = ctk.CTkLabel(self.scroll_frame, text="Chargement de la liste...", text_color="gray")
        self.loading_label.pack(pady=20)

    def _load_mods(self):
        def fetch():
            try:
                modpack = fetch_modpack()
                mods = modpack.get("mods", [])
                self.app.after(0, lambda: self._display_mods(mods))
            except Exception as e:
                self.app.after(0, lambda: self._display_error(str(e)))
        threading.Thread(target=fetch, daemon=True).start()

    def _display_mods(self, mods):
        self.loading_label.destroy()
        if not mods:
            ctk.CTkLabel(self.scroll_frame, text="Aucun mod trouvé.", text_color="gray").pack(pady=20)
            return

        for mod in sorted(mods, key=lambda m: m.get("name", "Inconnu").lower()):
            card = ctk.CTkFrame(self.scroll_frame, corner_radius=8)
            card.pack(fill="x", pady=4)
            ctk.CTkLabel(card, text=mod.get("name", "Mod Inconnu"), font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=12, pady=(8, 0))
            ctk.CTkLabel(card, text=mod.get("filename", "Fichier non spécifié"), font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w", padx=12, pady=(0, 8))

    def _display_error(self, error_msg):
        self.loading_label.configure(text=f"Erreur de chargement : {error_msg}", text_color="#FF5555")


class SettingsPage(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.max_ram = self._get_max_ram()
        self._build()
        
    def _get_max_ram(self):
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_ram_gb = stat.ullTotalPhys / (1024 ** 3)
            return max(2, int(total_ram_gb * 0.6))
        except Exception:
            return 8 # Sécurité si on ne peut pas lire la RAM (ex: macOS/Linux)

    def _build(self):
        title = ctk.CTkLabel(self, text="⚙️ Paramètres", font=ctk.CTkFont(size=20, weight="bold"))
        title.pack(anchor="w", padx=20, pady=(20, 6))

        # Conteneur défilable : la page contient plusieurs cartes qui dépassent la hauteur de la fenêtre
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=0, pady=(0, 10))

        card = ctk.CTkFrame(container, corner_radius=12)
        card.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(card, text="Mémoire RAM allouée", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(card, text="Le curseur est limité à 60% de votre RAM totale pour préserver votre système.", font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w", padx=16, pady=(0, 12))
        
        current_ram = min(self.app.settings.get('ram', 6), self.max_ram)
        if self.app.settings.get('ram', 6) > self.max_ram:
            self.app.settings['ram'] = current_ram
            self.app._save_settings()

        self.ram_val_label = ctk.CTkLabel(card, text=f"{current_ram} Go", font=ctk.CTkFont(size=14))
        self.ram_val_label.pack(anchor="w", padx=16)

        steps = max(1, self.max_ram - 2)
        self.slider = ctk.CTkSlider(card, from_=2, to=self.max_ram, number_of_steps=steps, command=self._on_slider_change)
        self.slider.set(current_ram)
        self.slider.pack(fill="x", padx=16, pady=(10, 20))

        # --- Options de jeu ---
        card2 = ctk.CTkFrame(container, corner_radius=12)
        card2.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(card2, text="Options de jeu", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16, pady=(16, 8))

        self.opt_switch = ctk.CTkSwitch(card2, text="Flags d'optimisation (Aikar — recommandé)", command=self._toggle_opt)
        self.opt_switch.pack(anchor="w", padx=16, pady=4)
        if self.app.settings.get("optimized_flags", True):
            self.opt_switch.select()

        self.fs_switch = ctk.CTkSwitch(card2, text="Lancer en plein écran", command=self._toggle_fullscreen)
        self.fs_switch.pack(anchor="w", padx=16, pady=4)
        if self.app.settings.get("fullscreen", False):
            self.fs_switch.select()

        self.close_switch = ctk.CTkSwitch(card2, text="Fermer le launcher quand le jeu démarre", command=self._toggle_close)
        self.close_switch.pack(anchor="w", padx=16, pady=4)
        if self.app.settings.get("close_on_launch", False):
            self.close_switch.select()

        # Résolution personnalisée
        res_label = ctk.CTkLabel(card2, text="Résolution personnalisée (laisser vide = par défaut)", font=ctk.CTkFont(size=11), text_color="gray")
        res_label.pack(anchor="w", padx=16, pady=(12, 2))
        res_row = ctk.CTkFrame(card2, fg_color="transparent")
        res_row.pack(anchor="w", fill="x", padx=16, pady=(0, 16))

        self.w_entry = ctk.CTkEntry(res_row, placeholder_text="Largeur (1920)", width=140)
        self.w_entry.pack(side="left", padx=(0, 8))
        self.w_entry.insert(0, str(self.app.settings.get("res_width", "")))
        self.w_entry.bind("<KeyRelease>", self._save_resolution)

        ctk.CTkLabel(res_row, text="×").pack(side="left")

        self.h_entry = ctk.CTkEntry(res_row, placeholder_text="Hauteur (1080)", width=140)
        self.h_entry.pack(side="left", padx=8)
        self.h_entry.insert(0, str(self.app.settings.get("res_height", "")))
        self.h_entry.bind("<KeyRelease>", self._save_resolution)

        # --- Maintenance ---
        card3 = ctk.CTkFrame(container, corner_radius=12)
        card3.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(card3, text="Maintenance", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(card3, text="Réinstalle Minecraft et Forge si le jeu refuse de démarrer ou plante.", font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w", padx=16, pady=(0, 8))
        ctk.CTkButton(card3, text="🔧 Réparer / Réinstaller", fg_color="#8B2500", hover_color="#A52A00", command=self.app.repair_installation).pack(anchor="w", padx=16, pady=(0, 16))

    def _on_slider_change(self, value):
        ram = int(value)
        self.ram_val_label.configure(text=f"{ram} Go")
        self.app.settings["ram"] = ram
        self.app._save_settings()

    def _toggle_opt(self):
        self.app.settings["optimized_flags"] = bool(self.opt_switch.get())
        self.app._save_settings()

    def _toggle_fullscreen(self):
        self.app.settings["fullscreen"] = bool(self.fs_switch.get())
        self.app._save_settings()

    def _toggle_close(self):
        self.app.settings["close_on_launch"] = bool(self.close_switch.get())
        self.app._save_settings()

    def _save_resolution(self, event=None):
        self.app.settings["res_width"] = self.w_entry.get().strip()
        self.app.settings["res_height"] = self.h_entry.get().strip()
        self.app._save_settings()


class LauncherApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"Community Craft Launcher v{LAUNCHER_VERSION}")
        self.geometry("720x520")
        self.resizable(False, False)
        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass
        self.session = None
        self.home_page = None
        self.minecraft_dir = os.path.join(os.getenv("APPDATA"), ".community-craft-v3")
        self.settings = self._load_settings()
        self._build()
        self._auto_connect()
        self._check_update()
        self._start_rich_presence()

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(self, self._navigate)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")

        self._show_home()

    def _load_settings(self):
        settings_path = os.path.join(os.getenv("APPDATA"), ".community-craft-v3", "launcher_settings.json")
        default_settings = {
            "ram": 6,                  # 6 Go par défaut
            "optimized_flags": True,   # Flags Aikar activés par défaut
            "fullscreen": False,
            "close_on_launch": False,
            "res_width": "",
            "res_height": "",
        }
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r") as f:
                    default_settings.update(json.load(f))
            except:
                pass
        return default_settings

    def _save_settings(self):
        settings_path = os.path.join(os.getenv("APPDATA"), ".community-craft-v3", "launcher_settings.json")
        try:
            with open(settings_path, "w") as f:
                json.dump(self.settings, f)
        except Exception as e:
            print(f"Erreur sauvegarde config : {e}")

    def _auto_connect(self):
        try:
            session = load_session()
            if session:
                print(f"Session locale trouvée : {session}")
                self.session = session
                self._on_connected(session["username"])
                return
        except Exception as e:
            print(f"Erreur auto-connect : {e}")

    def _connect_minecraft(self):
        self._login_offline()

    def _login_offline(self):
        dialog = ctk.CTkInputDialog(text="Entre ton pseudo :", title="Connexion Hors-Ligne")
        username = dialog.get_input()
        
        if username:
            try:
                session = login_offline(username)
                self.session = session
                self._on_connected(session["username"])
            except Exception as e:
                if self.home_page:
                    self.home_page.set_status(f"Erreur : {e}", "#FF5555")
        else:
            if self.home_page:
                self.home_page.set_status("Connexion annulée.", "gray")

    def _on_connected(self, username: str):
        self.sidebar.set_user(username)
        if self.home_page:
            self.home_page.set_connected(username)
            self.home_page.set_status(f"Bonjour {username} !", "#00BFFF")

    def _navigate(self, page):
        for widget in self.content.winfo_children():
            widget.destroy()
        self.home_page = None
        if page == "home":
            self._show_home()
            if self.session:
                self._on_connected(self.session["username"])
        elif page == "mods":
            page_frame = ModsPage(self.content, self)
            page_frame.pack(fill="both", expand=True)
        elif page == "settings":
            page_frame = SettingsPage(self.content, self)
            page_frame.pack(fill="both", expand=True)

    def _show_home(self):
        self.home_page = HomePage(self.content, self)
        self.home_page.pack(fill="both", expand=True)

    def _launch_game(self):
        if not self.session:
            self.home_page.set_status("Connecte-toi d'abord !", "#FF5555")
            return
        if self.home_page:
            self.home_page.play_btn.configure(state="disabled", text="Vérification des mods...")
            self.home_page.set_status("Synchronisation des mods...", "gray")
            self.home_page.show_progress()

            def on_progress(mod_name, status):
                if status == "downloading" and self.home_page:
                    self.after(0, lambda: self.home_page.set_status(f"Téléchargement : {mod_name}", "gray"))
                elif status == "deleting" and self.home_page:
                    self.after(0, lambda: self.home_page.set_status(f"Suppression : {mod_name}", "gray"))

            def on_overall(done, total):
                if self.home_page:
                    frac = done / total if total else 1
                    self.after(0, lambda: self.home_page.set_progress(frac))

            def on_complete(error):
                if error:
                    if self.home_page:
                        self.after(0, self.home_page.hide_progress)
                        self.after(0, lambda: self.home_page.set_status(f"Erreur mods : {error}", "#FF5555"))
                        self.after(0, lambda: self.home_page.play_btn.configure(state="normal", text="Jouer"))
                else:
                    self.after(0, self._lancer_minecraft)

            sync_mods(on_progress=on_progress, on_complete=on_complete, on_overall=on_overall)

    def _ensure_java(self, minecraft_dir):
        """Installe (si besoin) et renvoie le chemin du Java fourni par Mojang.

        Le jeu en a besoin pour tourner ET l'installateur Forge en a besoin pour s'installer.
        En l'utilisant, le launcher fonctionne même sur une machine sans aucun Java installé.
        Renvoie None si l'opération échoue (on retombe alors sur le Java système).
        """
        try:
            runtime = minecraft_launcher_lib.runtime
            info = runtime.get_version_runtime_information(MC_VERSION, minecraft_dir)
            if not info:
                return None
            jvm_name = info["name"]
            if not runtime.get_executable_path(jvm_name, minecraft_dir):
                if self.home_page:
                    self.after(0, lambda: self.home_page.set_status("Installation de Java...", "gray"))
                runtime.install_jvm_runtime(jvm_name, minecraft_dir)
            return runtime.get_executable_path(jvm_name, minecraft_dir)
        except Exception as e:
            print(f"Impossible de préparer le Java de Mojang : {e}")
            return None

    def _build_jvm_args(self):
        """Construit les arguments JVM selon les réglages (flags Aikar optionnels)."""
        ram = self.settings.get("ram", 6)
        if self.settings.get("optimized_flags", True):
            # Aikar recommande Xms == Xmx pour une allocation stable
            return [f"-Xmx{ram}G", f"-Xms{ram}G"] + AIKARS_FLAGS
        return [f"-Xmx{ram}G", "-Xms2G"]

    def _apply_options_txt(self):
        """Applique le réglage plein écran dans le options.txt de Minecraft."""
        value = "true" if self.settings.get("fullscreen") else "false"
        options_path = os.path.join(self.minecraft_dir, "options.txt")
        # Si le fichier n'existe pas et qu'on ne veut pas le plein écran, on ne touche à rien
        if not os.path.exists(options_path) and value == "false":
            return
        try:
            lines = []
            if os.path.exists(options_path):
                with open(options_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.read().splitlines()
            found = False
            for idx, line in enumerate(lines):
                if line.startswith("fullscreen:"):
                    lines[idx] = f"fullscreen:{value}"
                    found = True
                    break
            if not found:
                lines.append(f"fullscreen:{value}")
            with open(options_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            print(f"options.txt : {e}")

    def _read_log_tail(self, log_path, lines=15):
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().splitlines()
            return "\n".join(content[-lines:]) or "(log vide)"
        except Exception:
            return "(log indisponible)"

    def open_game_folder(self):
        """Ouvre le dossier d'installation du jeu dans l'explorateur."""
        try:
            os.makedirs(self.minecraft_dir, exist_ok=True)
            os.startfile(self.minecraft_dir)
        except Exception as e:
            if self.home_page:
                self.home_page.set_status(f"Impossible d'ouvrir le dossier : {e}", "#FF5555")

    def open_log(self):
        """Ouvre le dernier log de lancement."""
        log_path = os.path.join(self.minecraft_dir, "launcher_log.txt")
        if os.path.exists(log_path):
            try:
                os.startfile(log_path)
            except Exception as e:
                if self.home_page:
                    self.home_page.set_status(f"Impossible d'ouvrir le log : {e}", "#FF5555")
        elif self.home_page:
            self.home_page.set_status("Aucun log pour le moment (lance le jeu d'abord).", "gray")

    def open_discord(self):
        webbrowser.open(DISCORD_INVITE)

    def repair_installation(self):
        """Supprime Minecraft/Forge installés pour forcer une réinstallation propre."""
        if not messagebox.askyesno(
            "Réparer l'installation",
            "Cela va supprimer Minecraft et Forge installés.\n"
            "Tout sera réinstallé automatiquement au prochain clic sur Jouer.\n\n"
            "Continuer ?"
        ):
            return
        try:
            versions_dir = os.path.join(self.minecraft_dir, "versions")
            if os.path.exists(versions_dir):
                shutil.rmtree(versions_dir, ignore_errors=True)
            messagebox.showinfo("Réparer", "Installation réinitialisée.\nClique sur « Jouer » pour tout réinstaller.")
        except Exception as e:
            messagebox.showerror("Erreur", f"Réparation impossible : {e}")

    def _check_update(self):
        """Vérifie au démarrage si une nouvelle version du launcher est disponible."""
        def check():
            try:
                resp = requests.get(f"{UPDATE_URL}?t={int(time.time())}", timeout=8)
                if resp.status_code != 200:
                    return
                data = resp.json()
                latest = str(data.get("version", "")).strip()
                if latest and self._is_newer(latest, LAUNCHER_VERSION):
                    url = data.get("download_url", "")
                    self.after(0, lambda: self._prompt_update(latest, url))
            except Exception:
                pass  # Pas de réseau / fichier absent → on ignore silencieusement
        threading.Thread(target=check, daemon=True).start()

    @staticmethod
    def _is_newer(remote, local):
        def parse(v):
            return [int(x) for x in re.findall(r"\d+", v)]
        return parse(remote) > parse(local)

    def _prompt_update(self, version, url):
        if messagebox.askyesno(
            "Mise à jour disponible",
            f"Une nouvelle version du launcher est disponible (v{version}).\n"
            f"Tu utilises la v{LAUNCHER_VERSION}.\n\nVeux-tu la télécharger maintenant ?"
        ):
            if url:
                webbrowser.open(url)

    def _start_rich_presence(self):
        """Active le Discord Rich Presence si configuré (optionnel)."""
        if not DISCORD_CLIENT_ID:
            return

        def run():
            try:
                from pypresence import Presence
                rpc = Presence(DISCORD_CLIENT_ID)
                rpc.connect()
                self._rpc = rpc
                rpc.update(
                    state="Sur le launcher",
                    details=SERVER_NAME,
                    large_image="logo",
                    start=int(time.time()),
                )
            except Exception as e:
                print(f"Rich Presence indisponible : {e}")
        threading.Thread(target=run, daemon=True).start()

    def _monitor_game(self, proc, log_path):
        """Surveille le jeu ~20s après le lancement pour détecter un crash immédiat."""
        def monitor():
            crashed = False
            for _ in range(20):
                code = proc.poll()
                if code is not None:
                    crashed = (code != 0)
                    break
                time.sleep(1)

            if crashed:
                tail = self._read_log_tail(log_path)

                def show_crash():
                    if self.home_page:
                        self.home_page.set_status("❌ Le jeu a crashé au démarrage (voir le log).", "#FF5555")
                        self.home_page.play_btn.configure(state="normal", text="Jouer")
                    messagebox.showerror(
                        "Le jeu a crashé",
                        "Minecraft s'est fermé juste après le lancement.\n\n"
                        "Dernières lignes du log :\n\n" + tail
                    )
                self.after(0, show_crash)
            else:
                def ok():
                    if self.home_page:
                        self.home_page.set_status("✅ Jeu lancé avec succès !", "#00BFFF")
                        self.home_page.play_btn.configure(state="normal", text="Jouer")
                    if self.settings.get("close_on_launch"):
                        self.destroy()
                self.after(0, ok)
        threading.Thread(target=monitor, daemon=True).start()

    def _lancer_minecraft(self):
        if self.home_page:
            self.home_page.set_status("Lancement de Minecraft...", "#00BFFF")
            self.home_page.play_btn.configure(text="En cours...")
            self.home_page.show_progress()

        def launch():
            try:
                # Le dossier où le jeu sera installé (isolé du vrai .minecraft pour éviter les conflits)
                minecraft_dir = self.minecraft_dir
                forge_version = f"{MC_VERSION}-{FORGE_VERSION}"
                version_id = f"{MC_VERSION}-forge-{FORGE_VERSION}"
                version_dir = os.path.join(minecraft_dir, "versions", version_id)

                # Callback de progression branché sur la barre (setMax / setProgress / setStatus)
                progress_state = {"max": 1}

                def cb_status(text):
                    if self.home_page:
                        self.after(0, lambda: self.home_page.set_status(text, "gray"))

                def cb_max(value):
                    progress_state["max"] = max(1, value)

                def cb_progress(value):
                    frac = value / progress_state["max"]
                    if self.home_page:
                        self.after(0, lambda: self.home_page.set_progress(frac))

                callback = {"setStatus": cb_status, "setProgress": cb_progress, "setMax": cb_max}

                # Ne réinstalle Forge que si le dossier n'existe pas
                if not os.path.exists(version_dir):
                    if self.home_page:
                        self.after(0, lambda: self.home_page.set_status("Installation de Minecraft (1/2)...", "gray"))
                    # Installer Vanilla (télécharge aussi le Java 17 fourni par Mojang)
                    minecraft_launcher_lib.install.install_minecraft_version(MC_VERSION, minecraft_dir, callback=callback)

                    # Récupère le Java de Mojang pour ne PAS dépendre d'un Java installé sur la machine.
                    # Sans ça, l'installateur Forge plante sur les PC sans Java (cas de ton ami).
                    java_path = self._ensure_java(minecraft_dir)

                    if self.home_page:
                        self.after(0, lambda: self.home_page.set_status("Installation de Forge (2/2)...", "gray"))
                    # Installer Forge en lui imposant le Java de Mojang
                    minecraft_launcher_lib.forge.install_forge_version(forge_version, minecraft_dir, callback=callback, java=java_path)
                else:
                    if self.home_page:
                        self.after(0, lambda: self.home_page.set_status("Vérification des fichiers...", "gray"))
                    java_path = self._ensure_java(minecraft_dir)

                options = {
                    "username": self.session["username"],
                    "uuid": self.session["uuid"],
                    "token": self.session["access_token"],
                    "jvmArguments": self._build_jvm_args(),
                }
                # Force le jeu à utiliser le Java de Mojang plutôt qu'un Java système éventuellement incompatible
                if java_path:
                    options["executablePath"] = java_path

                # Résolution personnalisée
                rw = str(self.settings.get("res_width", "")).strip()
                rh = str(self.settings.get("res_height", "")).strip()
                if rw.isdigit() and rh.isdigit():
                    options["customResolution"] = True
                    options["resolutionWidth"] = rw
                    options["resolutionHeight"] = rh

                # Plein écran via options.txt
                self._apply_options_txt()

                if self.home_page:
                    self.after(0, self.home_page.hide_progress)
                    self.after(0, lambda: self.home_page.set_status("Démarrage du jeu...", "#00BFFF"))

                # Génère la commande Java et lance le jeu en arrière-plan
                command = minecraft_launcher_lib.command.get_minecraft_command(version_id, minecraft_dir, options)

                # Écrit les erreurs dans un fichier texte pour qu'on puisse les lire tranquillement
                log_path = os.path.join(minecraft_dir, "launcher_log.txt")
                log_file = open(log_path, "w")

                kwargs = {}
                if sys.platform == "win32":
                    kwargs["creationflags"] = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

                proc = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs)
                self.after(0, lambda: self.home_page.set_status("Jeu lancé ! Vérification du démarrage...", "#00BFFF"))

                # Surveille le process pour détecter un crash au démarrage
                self._monitor_game(proc, log_path)

            except Exception as e:
                if self.home_page:
                    self.after(0, self.home_page.hide_progress)
                    self.after(0, lambda: self.home_page.set_status(f"Erreur : {e}", "#FF5555"))
                    self.after(0, lambda: self.home_page.play_btn.configure(state="normal", text="Jouer"))

        threading.Thread(target=launch, daemon=True).start()


if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()