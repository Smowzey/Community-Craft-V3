import customtkinter as ctk
import subprocess
import threading
import os
import json
import ctypes
import sys
import re
import time
import gc
import hashlib
import shutil
import webbrowser
import urllib.parse
import requests
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk
import minecraft_launcher_lib
from auth import (
    SESSION_MICROSOFT,
    SESSION_OFFLINE,
    clear_session,
    load_session,
    login_microsoft,
    login_offline,
    refresh_microsoft,
    validate_username,
)
from mod_manager import sync_mods, fetch_modpack, validate_mods, set_shaders_enabled

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SERVER_NAME = "Community Craft V3"
MC_VERSION = "1.20.1"
FORGE_VERSION = "47.3.0"
PACK_VERSION = "v3.1"

# Version du launcher (utilisée pour l'auto-update)
LAUNCHER_VERSION = "3.2.0"

# Sources distantes (même dépôt GitHub que le modpack)
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/Smowzey/community-craft-v3/main"
NEWS_URL = f"{GITHUB_RAW_BASE}/news.json"
UPDATE_URL = f"{GITHUB_RAW_BASE}/launcher_version.json"

# Hôtes acceptés pour télécharger une mise à jour du launcher.
# Un .exe téléchargé puis exécuté : on ne prend le risque que depuis GitHub.
UPDATE_ALLOWED_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    "codeload.github.com",
)

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

# --- Palette de l'interface (sombre + accent turquoise) ---------------------
COLORS = {
    "bg":        "#0B0B0E",
    "rail":      "#0E0E12",
    "surface":   "#151519",
    "surface_2": "#1B1B21",
    "hover":     "#232329",
    "border":    "#26262D",
    "text":      "#F1F1F4",
    "text_dim":  "#8A8A95",
    "text_mute": "#5C5C67",
    "accent":    "#3ECFC4",
    "accent_h":  "#31B0A6",
    "accent_bg": "#12312F",
    "danger":    "#E5484D",
    "danger_h":  "#C2393D",
    "ok":        "#3FB950",
    "warn":      "#E0A020",
}

FONT_FAMILY = "Segoe UI"

# --- Optimisation JVM -------------------------------------------------------
# Base éprouvée (variante « client » des flags Aikar : pauses GC plus courtes,
# donc moins de micro-freezes en jeu qu'un réglage serveur).
BASE_JVM_FLAGS = [
    "-XX:+UseG1GC",
    "-XX:+ParallelRefProcEnabled",
    "-XX:MaxGCPauseMillis=37",
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+DisableExplicitGC",
    "-XX:+AlwaysPreTouch",
    "-XX:G1NewSizePercent=30",
    "-XX:G1MaxNewSizePercent=40",
    "-XX:G1HeapRegionSize=16M",
    "-XX:G1ReservePercent=20",
    "-XX:G1HeapWastePercent=5",
    "-XX:G1MixedGCCountTarget=4",
    "-XX:InitiatingHeapOccupancyPercent=15",
    "-XX:G1MixedGCLiveThresholdPercent=90",
    "-XX:G1RSetUpdatingPauseTimePercent=5",
    "-XX:SurvivorRatio=32",
    "-XX:+PerfDisableSharedMem",
    "-XX:MaxTenuringThreshold=1",
    "-Dlog4j2.formatMsgNoLookups=true",   # sécurité : neutralise Log4Shell
    "-Djava.net.preferIPv4Stack=true",
]

# Flags plus agressifs : ils n'existent pas sur toutes les versions de Java.
# Ceux que la JVM refuse sont retirés automatiquement (voir _supported_jvm_flags),
# donc en ajouter ici ne peut pas empêcher le jeu de démarrer.
EXTRA_JVM_FLAGS = [
    "-XX:+UseNUMA",
    "-XX:-DontCompileHugeMethods",
    "-XX:MaxNodeLimit=240000",
    "-XX:NodeLimitFudgeFactor=8000",
    "-XX:+UseVectorCmov",
    "-XX:+UseFPUForSpilling",
    "-XX:AllocatePrefetchStyle=3",
    "-XX:G1SATBBufferEnqueueingThresholdPercent=30",
    "-XX:G1ConcMarkStepDurationMillis=5.0",   # attend un décimal, « 5 » est refusé
    "-XX:G1ConcRSHotCardLimit=16",
]

# --- Profils de performance (écrits dans options.txt avant le lancement) ----
PERF_PROFILES = {
    "none": {},
    "perf": {
        "renderDistance": "8",
        "simulationDistance": "6",
        "graphicsMode": "0",
        "particles": "2",
        "entityShadows": "false",
        "entityDistanceScaling": "0.5",
        "biomeBlendRadius": "0",
        "mipmapLevels": "0",
        "ao": "false",
        "renderClouds": '"false"',
        "maxFps": "120",
    },
    "balanced": {
        "renderDistance": "12",
        "simulationDistance": "8",
        "graphicsMode": "0",
        "particles": "1",
        "entityShadows": "true",
        "entityDistanceScaling": "0.75",
        "biomeBlendRadius": "1",
        "mipmapLevels": "2",
        "ao": "true",
        "renderClouds": '"false"',
        "maxFps": "144",
    },
    "quality": {
        "renderDistance": "16",
        "simulationDistance": "10",
        "graphicsMode": "1",
        "particles": "0",
        "entityShadows": "true",
        "entityDistanceScaling": "1.0",
        "biomeBlendRadius": "3",
        "mipmapLevels": "4",
        "ao": "true",
        "renderClouds": '"true"',
        "maxFps": "260",
    },
}

PROFILE_LABELS = {
    "Ne pas modifier": "none",
    "Performance": "perf",
    "Équilibré": "balanced",
    "Qualité": "quality",
}
SHADER_LABELS = {"Auto": "auto", "Activés": "on", "Désactivés": "off"}

# Classes de priorité Windows (CreateProcess / SetPriorityClass)
PRIORITY_IDLE = 0x00000040
PRIORITY_BELOW_NORMAL = 0x00004000
PRIORITY_NORMAL = 0x00000020
PRIORITY_ABOVE_NORMAL = 0x00008000
CREATE_NO_WINDOW = 0x08000000


def _kernel32():
    """kernel32 avec les signatures déclarées.

    Sans argtypes, ctypes passe le pseudo-handle de GetCurrentProcess() sur
    32 bits : en 64 bits le handle est tronqué et SetPriorityClass échoue
    silencieusement.
    """
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.GetCurrentProcess.restype = ctypes.c_void_p
    lib.GetCurrentProcess.argtypes = []
    lib.SetPriorityClass.restype = ctypes.c_int
    lib.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    return lib


def resource_path(relative_path):
    """Obtient le chemin absolu de la ressource, compatible avec PyInstaller"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


_FONT_CACHE = {}


def font(size=13, weight="normal"):
    """Petite fabrique de polices mise en cache (évite d'en recréer 200)."""
    key = (size, weight)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)
    return _FONT_CACHE[key]


def human_delay(timestamp):
    """« il y a 3 h », « à l'instant »... à partir d'un timestamp epoch."""
    if not timestamp:
        return "jamais"
    delta = max(0, int(time.time() - timestamp))
    if delta < 60:
        return "à l'instant"
    if delta < 3600:
        return f"il y a {delta // 60} min"
    if delta < 86400:
        return f"il y a {delta // 3600} h"
    return f"il y a {delta // 86400} j"


def make_hero_image(width, height, radius=16):
    """Fabrique le fond dégradé de la bannière d'accueil.

    On dessine deux halos colorés en tout petit puis on agrandit : le flou est
    quasi gratuit à cette taille, et le rendu est identique une fois étiré.
    """
    small_w, small_h = max(1, width // 6), max(1, height // 6)
    glow = Image.new("RGB", (small_w, small_h), "#12121A")
    draw = ImageDraw.Draw(glow)
    # Halo violet à gauche, halo turquoise à droite (comme un fond de menu)
    draw.ellipse([-small_w * 0.15, -small_h * 0.6, small_w * 0.55, small_h * 1.1], fill="#4C1D95")
    draw.ellipse([small_w * 0.30, small_h * 0.25, small_w * 0.85, small_h * 1.7], fill="#7E22CE")
    draw.ellipse([small_w * 0.72, -small_h * 0.35, small_w * 1.25, small_h * 0.9], fill="#0F766E")
    glow = glow.filter(ImageFilter.GaussianBlur(small_w // 8 or 1))
    img = glow.resize((width, height), Image.BICUBIC)

    # Voile sombre plus dense à gauche : le texte blanc doit rester lisible
    ramp = Image.new("L", (width, 1))
    ramp.putdata([int(215 * max(0.0, 1.0 - (x / (width * 0.72)) ** 1.3)) for x in range(width)])
    shade = ramp.resize((width, height))
    img = Image.composite(Image.new("RGB", (width, height), "#08080B"), img, shade)

    # Coins arrondis « à la main » : un widget posé par-dessus ne saurait pas les gérer
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, width - 1, height - 1], radius=radius, fill=255)
    return Image.composite(img, Image.new("RGB", (width, height), COLORS["bg"]), mask)


def make_avatar_image(letter, size=42):
    """Pastille d'avatar avec l'initiale du pseudo (dessinée en 4x puis réduite)."""
    scale = 4
    box = size * scale
    img = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, box - 1, box - 1], radius=int(box * 0.32), fill=COLORS["accent"])
    try:
        pil_font = ImageFont.truetype("segoeuib.ttf", int(box * 0.52))
    except Exception:
        pil_font = ImageFont.load_default()
    draw.text((box / 2, box / 2 - box * 0.04), (letter or "?").upper()[:1],
              fill="#08201F", anchor="mm", font=pil_font)
    return img.resize((size, size), Image.LANCZOS)


class Tooltip:
    """Info-bulle minimaliste pour les icônes du rail (une seule fenêtre réutilisée)."""

    _window = None
    _label = None

    @classmethod
    def attach(cls, widget, text):
        widget.bind("<Enter>", lambda e: cls._show(widget, text), add="+")
        widget.bind("<Leave>", lambda e: cls._hide(), add="+")
        widget.bind("<Button-1>", lambda e: cls._hide(), add="+")

    @classmethod
    def _show(cls, widget, text):
        try:
            if cls._window is None or not cls._window.winfo_exists():
                cls._window = tk.Toplevel(widget)
                cls._window.overrideredirect(True)
                cls._window.attributes("-topmost", True)
                cls._label = tk.Label(
                    cls._window, text=text, bg=COLORS["surface_2"], fg=COLORS["text"],
                    font=(FONT_FAMILY, 9), padx=8, pady=4, bd=0,
                    highlightthickness=1, highlightbackground=COLORS["border"],
                )
                cls._label.pack()
            cls._label.configure(text=text)
            x = widget.winfo_rootx() + widget.winfo_width() + 10
            y = widget.winfo_rooty() + widget.winfo_height() // 2 - 12
            cls._window.geometry(f"+{x}+{y}")
            cls._window.deiconify()
        except Exception:
            pass

    @classmethod
    def _hide(cls):
        try:
            if cls._window is not None and cls._window.winfo_exists():
                cls._window.withdraw()
        except Exception:
            pass


class Sidebar(ctk.CTkFrame):
    """Rail d'icônes à gauche : logo, navigation, raccourcis."""

    def __init__(self, parent, on_navigate, app):
        super().__init__(parent, width=68, corner_radius=0, fg_color=COLORS["rail"])
        self.on_navigate = on_navigate
        self.app = app
        self.nav_buttons = {}
        self._build()

    def _build(self):
        # Les deux : sans pack_propagate, le rail s'élargit à la taille de ses enfants
        self.grid_propagate(False)
        self.pack_propagate(False)

        try:
            logo = ctk.CTkImage(Image.open(resource_path("logo.png")), size=(34, 34))
            ctk.CTkLabel(self, image=logo, text="").pack(pady=(18, 14))
        except Exception:
            ctk.CTkLabel(self, text="⬡", font=font(26, "bold"),
                         text_color=COLORS["accent"]).pack(pady=(18, 14))

        tk.Frame(self, height=1, bg=COLORS["border"], bd=0,
                 highlightthickness=0).pack(fill="x", padx=16, pady=(0, 10))

        for icon, key, tip in [("⌂", "home", "Accueil"),
                               ("▦", "mods", "Mods"),
                               ("⚙", "settings", "Paramètres")]:
            btn = ctk.CTkButton(
                self, text=icon, width=44, height=44, corner_radius=12,
                font=font(19), fg_color="transparent",
                hover_color=COLORS["hover"], text_color=COLORS["text_dim"],
                command=lambda k=key: self.on_navigate(k),
            )
            btn.pack(pady=3)
            Tooltip.attach(btn, tip)
            self.nav_buttons[key] = btn

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", pady=(0, 16))
        for icon, cmd, tip in [("📁", self.app.open_game_folder, "Dossier du jeu"),
                               ("💬", self.app.open_discord, "Discord")]:
            btn = ctk.CTkButton(
                bottom, text=icon, width=44, height=38, corner_radius=12,
                font=font(15), fg_color="transparent",
                hover_color=COLORS["hover"], text_color=COLORS["text_dim"], command=cmd,
            )
            btn.pack(pady=2)
            Tooltip.attach(btn, tip)

    def set_active(self, key):
        for name, btn in self.nav_buttons.items():
            active = name == key
            btn.configure(
                fg_color=COLORS["accent_bg"] if active else "transparent",
                text_color=COLORS["accent"] if active else COLORS["text_dim"],
            )


class TopBar(ctk.CTkFrame):
    """Bandeau supérieur : nom du pack, build, état de la session."""

    def __init__(self, parent):
        super().__init__(parent, height=46, corner_radius=0, fg_color=COLORS["bg"])
        self.grid_propagate(False)
        self.pack_propagate(False)

        left = ctk.CTkFrame(self, fg_color="transparent")
        left.pack(side="left", padx=20)
        ctk.CTkLabel(left, text=SERVER_NAME, font=font(13, "bold"),
                     text_color=COLORS["text"]).pack(side="left")
        ctk.CTkLabel(left, text="│", font=font(12),
                     text_color=COLORS["border"]).pack(side="left", padx=10)
        ctk.CTkLabel(left, text=f"Build {LAUNCHER_VERSION}", font=font(11),
                     text_color=COLORS["text_mute"]).pack(side="left")
        ctk.CTkLabel(left, text="│", font=font(12),
                     text_color=COLORS["border"]).pack(side="left", padx=10)
        self.state_label = ctk.CTkLabel(left, text="● Hors ligne", font=font(11),
                                        text_color=COLORS["text_mute"])
        self.state_label.pack(side="left")

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="right", padx=20)
        for tag in [f"Forge {FORGE_VERSION}", f"MC {MC_VERSION}", PACK_VERSION]:
            ctk.CTkLabel(right, text=tag, font=font(10), fg_color=COLORS["surface"],
                         text_color=COLORS["text_dim"], corner_radius=6,
                         padx=9, pady=3).pack(side="left", padx=3)

    def set_state(self, text, color):
        self.state_label.configure(text=text, text_color=color)


class HeroBanner(tk.Canvas):
    """Bannière d'accueil : fond dégradé + accueil + gros bouton de lancement.

    C'est un Canvas et non un Frame : c'est le seul moyen d'écrire du texte
    par-dessus une image sans que le fond du label vienne masquer le dégradé.
    """

    WIDTH = 744
    HEIGHT = 214

    def __init__(self, parent, app):
        self.app = app
        try:
            self.scale = ctk.ScalingTracker.get_widget_scaling(parent)
        except Exception:
            self.scale = 1.0

        w, h = self.px(self.WIDTH), self.px(self.HEIGHT)
        self._image = make_hero_image(w, h, radius=self.px(16))
        # Couleur du dégradé sous le bloc de lancement : les coins arrondis des
        # widgets posés dessus prennent le fond du canvas, autant qu'il se fonde.
        sample = self._image.getpixel((self.px(40), self.px(170)))
        self.bg_color = "#%02x%02x%02x" % sample

        super().__init__(parent, width=w, height=h, bg=self.bg_color,
                         highlightthickness=0, bd=0)
        self._photo = ImageTk.PhotoImage(self._image)
        self.create_image(0, 0, anchor="nw", image=self._photo)
        self._build()

    def px(self, value):
        return int(round(value * self.scale))

    def _build(self):
        self.create_text(self.px(30), self.px(30), anchor="nw", text="Content de te revoir,",
                         fill=COLORS["text_dim"], font=(FONT_FAMILY, -self.px(13)))
        self.greeting = self.create_text(self.px(30), self.px(50), anchor="nw", text="Invité",
                                         fill=COLORS["text"], font=(FONT_FAMILY, -self.px(27), "bold"))
        self.subline = self.create_text(self.px(31), self.px(90), anchor="nw",
                                        text="Dernière session : jamais",
                                        fill=COLORS["text_mute"], font=(FONT_FAMILY, -self.px(11)))

        # --- Bloc de lancement (bouton + bandeau de réglages) ---
        block = ctk.CTkFrame(self, fg_color="transparent", width=300, height=78)
        self.play_btn = ctk.CTkButton(
            block, text="LANCER", font=font(19, "bold"), height=52, width=300,
            corner_radius=10, fg_color=COLORS["accent"], hover_color=COLORS["accent_h"],
            text_color="#07211F", command=self.app.launch_game,
        )
        self.play_btn.pack(fill="x")
        self.launch_hint = ctk.CTkButton(
            block, text="PARAMÈTRES DE LANCEMENT  ▾", font=font(9, "bold"), height=24, width=300,
            corner_radius=8, fg_color=COLORS["surface"], hover_color=COLORS["hover"],
            text_color=COLORS["text_dim"], command=lambda: self.app.navigate("settings"),
        )
        self.launch_hint.pack(fill="x", pady=(4, 0))
        self.create_window(self.px(30), self.px(120), anchor="nw", window=block,
                           width=self.px(300), height=self.px(80))

        # --- Statut + barre de progression (à droite du bouton) ---
        self.status_item = self.create_text(
            self.px(356), self.px(132), anchor="nw", text="",
            fill=COLORS["text_dim"], font=(FONT_FAMILY, -self.px(11)), width=self.px(350),
        )
        self.progress_bar = ctk.CTkProgressBar(
            self, height=6, corner_radius=3, width=340,
            fg_color=COLORS["surface_2"], progress_color=COLORS["accent"],
        )
        self.progress_bar.set(0)
        self._progress_window = None

    # -- API utilisée par le launcher ------------------------------------
    def set_user(self, username):
        self.itemconfigure(self.greeting, text=username)

    def set_subline(self, text):
        self.itemconfigure(self.subline, text=text)

    def set_status(self, text, color=None):
        self.itemconfigure(self.status_item, text=text, fill=color or COLORS["text_dim"])

    def set_playable(self, enabled, text="LANCER"):
        self.play_btn.configure(
            state="normal" if enabled else "disabled",
            text=text,
            fg_color=COLORS["accent"] if enabled else COLORS["surface_2"],
            text_color="#07211F" if enabled else COLORS["text_mute"],
        )

    def show_progress(self):
        if self._progress_window is None:
            self._progress_window = self.create_window(
                self.px(356), self.px(160), anchor="nw", window=self.progress_bar,
                width=self.px(340), height=self.px(8),
            )
        self.progress_bar.set(0)
        self.itemconfigure(self._progress_window, state="normal")

    def hide_progress(self):
        if self._progress_window is not None:
            self.itemconfigure(self._progress_window, state="hidden")

    def set_progress(self, fraction):
        self.progress_bar.set(max(0.0, min(1.0, fraction)))


class HomePage(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._build()
        self._fetch_news()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self.hero = HeroBanner(self, self.app)
        self.hero.grid(row=0, column=0, sticky="w", padx=18, pady=(14, 12))

        # --- Trois tuiles de stats ---
        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", padx=18)
        stats.grid_columnconfigure((0, 1, 2), weight=1, uniform="stat")

        self.stats_labels = {}
        tiles = [("Mods installés", "…"),
                 ("RAM allouée", f"{self.app.settings.get('ram', 6)} Go"),
                 ("Version du pack", PACK_VERSION)]
        for col, (label, value) in enumerate(tiles):
            card = ctk.CTkFrame(stats, corner_radius=12, fg_color=COLORS["surface"],
                                border_width=1, border_color=COLORS["border"])
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0))
            val = ctk.CTkLabel(card, text=value, font=font(19, "bold"), text_color=COLORS["text"])
            val.pack(anchor="w", padx=14, pady=(10, 0))
            ctk.CTkLabel(card, text=label.upper(), font=font(9, "bold"),
                         text_color=COLORS["text_mute"]).pack(anchor="w", padx=14, pady=(0, 10))
            self.stats_labels[label] = val

        # --- Fil d'actualités ---
        news = ctk.CTkFrame(self, corner_radius=12, fg_color=COLORS["surface"],
                            border_width=1, border_color=COLORS["border"])
        news.grid(row=2, column=0, sticky="nsew", padx=18, pady=(12, 16))
        news.grid_columnconfigure(0, weight=1)
        news.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(news, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        ctk.CTkLabel(head, text="ACTUALITÉS", font=font(10, "bold"),
                     text_color=COLORS["text_mute"]).pack(side="left")
        ctk.CTkLabel(head, text="NOUVEAU", font=font(9, "bold"), fg_color=COLORS["accent_bg"],
                     text_color=COLORS["accent"], corner_radius=5,
                     padx=7, pady=2).pack(side="left", padx=8)

        self.news_box = ctk.CTkTextbox(
            news, fg_color="transparent", font=font(12), text_color=COLORS["text_dim"],
            wrap="word", activate_scrollbars=True, border_width=0,
        )
        self.news_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self._set_news(DEFAULT_NEWS)

    # -- Contenu dynamique ------------------------------------------------
    def _set_news(self, text):
        # On borne la taille : le fichier est distant, pas la peine d'afficher 1 Mo
        text = str(text)[:4000]
        self.news_box.configure(state="normal")
        self.news_box.delete("1.0", "end")
        self.news_box.insert("1.0", text)
        self.news_box.configure(state="disabled")

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
                self.app.ui(lambda: self._set_news(text))
            except Exception:
                pass  # On garde les actualités par défaut déjà affichées
        threading.Thread(target=fetch, daemon=True).start()

    def update_stats(self, mod_count=None):
        if mod_count is not None:
            self.stats_labels["Mods installés"].configure(text=str(mod_count))
        self.stats_labels["RAM allouée"].configure(text=f"{self.app.settings.get('ram', 6)} Go")

    def refresh_subline(self):
        last = human_delay(self.app.settings.get("last_launch"))
        self.hero.set_subline(f"Dernière session : {last}   ·   Forge {FORGE_VERSION} · MC {MC_VERSION}")

    # -- Compatibilité avec le reste du launcher --------------------------
    def set_connected(self, username):
        self.hero.set_user(username)
        self.hero.set_playable(True)
        self.refresh_subline()

    def set_status(self, text, color="gray"):
        self.hero.set_status(text, None if color == "gray" else color)

    def show_progress(self):
        self.hero.show_progress()

    def hide_progress(self):
        self.hero.hide_progress()

    def set_progress(self, fraction):
        self.hero.set_progress(fraction)

    @property
    def play_btn(self):
        return self.hero.play_btn


class SidePanel(ctk.CTkFrame):
    """Colonne de droite : session, infos et accès rapides."""

    def __init__(self, parent, app):
        super().__init__(parent, width=286, corner_radius=0, fg_color=COLORS["rail"])
        self.app = app
        self.grid_propagate(False)
        self.pack_propagate(False)
        self._avatar = None
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="SESSION", font=font(10, "bold"),
                     text_color=COLORS["text_mute"]).pack(anchor="w", padx=18, pady=(18, 8))

        user_card = ctk.CTkFrame(self, corner_radius=12, fg_color=COLORS["surface"],
                                 border_width=1, border_color=COLORS["border"])
        user_card.pack(fill="x", padx=16)

        row = ctk.CTkFrame(user_card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=12)
        self.avatar_label = ctk.CTkLabel(row, text="", width=42, height=42)
        self.avatar_label.pack(side="left")
        self.set_avatar("?")

        texts = ctk.CTkFrame(row, fg_color="transparent")
        texts.pack(side="left", padx=10, fill="x", expand=True)
        self.username_label = ctk.CTkLabel(texts, text="Non connecté", font=font(13, "bold"),
                                           text_color=COLORS["text"], anchor="w")
        self.username_label.pack(fill="x")
        self.status_dot = ctk.CTkLabel(texts, text="● Hors ligne", font=font(10),
                                       text_color=COLORS["text_mute"], anchor="w")
        self.status_dot.pack(fill="x")

        buttons = ctk.CTkFrame(user_card, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        self.login_btn = ctk.CTkButton(
            buttons, text="Se connecter", height=32, corner_radius=8, font=font(12),
            fg_color=COLORS["surface_2"], hover_color=COLORS["hover"],
            text_color=COLORS["text"], command=self.app.connect_minecraft,
        )
        self.login_btn.pack(side="left", fill="x", expand=True)
        self.logout_btn = ctk.CTkButton(
            buttons, text="⏻", width=32, height=32, corner_radius=8, font=font(13),
            fg_color=COLORS["surface_2"], hover_color=COLORS["danger"],
            text_color=COLORS["text_mute"], command=self.app.logout,
        )
        Tooltip.attach(self.logout_btn, "Se déconnecter")

        # --- Infos de l'installation ---
        ctk.CTkLabel(self, text="INSTALLATION", font=font(10, "bold"),
                     text_color=COLORS["text_mute"]).pack(anchor="w", padx=18, pady=(18, 8))

        info_card = ctk.CTkFrame(self, corner_radius=12, fg_color=COLORS["surface"],
                                 border_width=1, border_color=COLORS["border"])
        info_card.pack(fill="x", padx=16)
        self.info_values = {}
        for label in ("Mods", "RAM", "Profil", "Shaders"):
            line = ctk.CTkFrame(info_card, fg_color="transparent")
            line.pack(fill="x", padx=12, pady=5)
            ctk.CTkLabel(line, text=label, font=font(11),
                         text_color=COLORS["text_mute"]).pack(side="left")
            value = ctk.CTkLabel(line, text="—", font=font(11, "bold"), text_color=COLORS["text"])
            value.pack(side="right")
            self.info_values[label] = value

        # --- Accès rapides ---
        ctk.CTkLabel(self, text="ACCÈS RAPIDE", font=font(10, "bold"),
                     text_color=COLORS["text_mute"]).pack(anchor="w", padx=18, pady=(18, 8))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=16)
        for label, cmd in [("📁   Dossier du jeu", self.app.open_game_folder),
                           ("📄   Voir le log", self.app.open_log),
                           ("💬   Rejoindre le Discord", self.app.open_discord),
                           ("🔧   Réparer l'installation", self.app.repair_installation)]:
            ctk.CTkButton(
                actions, text=label, anchor="w", height=34, corner_radius=9, font=font(12),
                fg_color=COLORS["surface"], hover_color=COLORS["hover"],
                text_color=COLORS["text_dim"], command=cmd,
            ).pack(fill="x", pady=3)

        ctk.CTkLabel(self, text=f"Launcher v{LAUNCHER_VERSION}", font=font(10),
                     text_color=COLORS["text_mute"]).pack(side="bottom", pady=14)

    def set_avatar(self, letter):
        image = make_avatar_image(letter)
        self._avatar = ctk.CTkImage(image, size=(42, 42))
        self.avatar_label.configure(image=self._avatar, text="")

    def set_user(self, username, session_type=SESSION_OFFLINE):
        self.username_label.configure(text=username)
        if session_type == SESSION_MICROSOFT:
            self.status_dot.configure(text="● Compte Microsoft", text_color=COLORS["accent"])
        else:
            self.status_dot.configure(text="● Hors-ligne", text_color=COLORS["warn"])
        self.login_btn.configure(text="Changer de compte")
        self.logout_btn.pack(side="left", padx=(6, 0))
        self.set_avatar(username[:1])

    def set_disconnected(self):
        self.username_label.configure(text="Non connecté")
        self.status_dot.configure(text="● Aucun compte", text_color=COLORS["text_mute"])
        self.login_btn.configure(text="Se connecter")
        self.logout_btn.pack_forget()
        self.set_avatar("?")

    def update_info(self, mods=None):
        settings = self.app.settings
        if mods is not None:
            self.info_values["Mods"].configure(text=str(mods))
        self.info_values["RAM"].configure(text=f"{settings.get('ram', 6)} Go")
        profile = settings.get("perf_profile", "none")
        labels = {v: k for k, v in PROFILE_LABELS.items()}
        self.info_values["Profil"].configure(text=labels.get(profile, "Ne pas modifier"))
        shaders = {"auto": "Auto", "on": "Activés", "off": "Désactivés"}
        self.info_values["Shaders"].configure(text=shaders.get(settings.get("shaders", "auto"), "Auto"))


class LoginDialog(ctk.CTkToplevel):
    """Fenêtre de connexion : compte Microsoft ou mode hors-ligne (pseudo)."""

    def __init__(self, parent, current=""):
        super().__init__(parent)
        self.session = None
        self._cancel_event = threading.Event()
        self._busy = False

        self.title("Connexion")
        self.configure(fg_color=COLORS["bg"])
        self.resizable(False, False)
        self.transient(parent)

        ctk.CTkLabel(self, text="Connexion", font=font(17, "bold"),
                     text_color=COLORS["text"]).pack(padx=28, pady=(22, 2))
        ctk.CTkLabel(self, text="Choisis comment tu veux jouer.", font=font(11),
                     text_color=COLORS["text_mute"]).pack(padx=28, pady=(0, 16))

        # --- Compte Microsoft ---
        self.ms_btn = ctk.CTkButton(
            self, text="Se connecter avec Microsoft", width=320, height=42, corner_radius=9,
            font=font(13, "bold"), fg_color=COLORS["accent"], hover_color=COLORS["accent_h"],
            text_color="#07211F", command=self._login_microsoft,
        )
        self.ms_btn.pack(padx=28)
        ctk.CTkLabel(self, text="Compte Minecraft officiel : skins, capes et serveurs en ligne.\n"
                                "La saisie du mot de passe se fait chez Microsoft, dans ton navigateur.",
                     font=font(10), text_color=COLORS["text_mute"], justify="center").pack(
                         padx=28, pady=(6, 14))

        # --- Séparateur (grid : un frame de 1 px ne se dimensionne pas en pack) ---
        sep = ctk.CTkFrame(self, fg_color="transparent", height=18)
        sep.pack(fill="x", padx=28, pady=(0, 12))
        sep.grid_columnconfigure((0, 2), weight=1)
        # tk.Frame et non CTkFrame : CustomTkinter ne dessine pas un cadre de 1 px
        tk.Frame(sep, height=1, bg=COLORS["border"], bd=0, highlightthickness=0).grid(
            row=0, column=0, sticky="ew")
        ctk.CTkLabel(sep, text="ou", font=font(10), text_color=COLORS["text_mute"]).grid(
            row=0, column=1, padx=10)
        tk.Frame(sep, height=1, bg=COLORS["border"], bd=0, highlightthickness=0).grid(
            row=0, column=2, sticky="ew")

        # --- Mode hors-ligne ---
        ctk.CTkLabel(self, text="Mode hors-ligne", font=font(12, "bold"),
                     text_color=COLORS["text_dim"]).pack(padx=28, anchor="w")
        ctk.CTkLabel(self, text="3 à 16 caractères : lettres, chiffres et « _ »", font=font(10),
                     text_color=COLORS["text_mute"]).pack(padx=28, anchor="w", pady=(0, 6))

        self.entry = ctk.CTkEntry(
            self, width=320, height=38, corner_radius=9, font=font(13),
            fg_color=COLORS["surface"], border_color=COLORS["border"],
            text_color=COLORS["text"], placeholder_text="Pseudo",
        )
        self.entry.pack(padx=28)
        if current:
            self.entry.insert(0, current)

        self.offline_btn = ctk.CTkButton(
            self, text="Jouer hors-ligne", width=320, height=34, corner_radius=9, font=font(12),
            fg_color=COLORS["surface_2"], hover_color=COLORS["hover"], text_color=COLORS["text"],
            command=self._login_offline,
        )
        self.offline_btn.pack(padx=28, pady=(8, 0))

        self.message = ctk.CTkLabel(self, text="", font=font(10), text_color=COLORS["danger"],
                                    wraplength=320, justify="center")
        self.message.pack(padx=28, pady=(8, 0))

        self.cancel_btn = ctk.CTkButton(
            self, text="Annuler", width=320, height=30, corner_radius=8, font=font(11),
            fg_color="transparent", hover_color=COLORS["hover"],
            text_color=COLORS["text_mute"], command=self._cancel,
        )
        self.cancel_btn.pack(padx=28, pady=(8, 20))

        self.entry.bind("<Return>", lambda e: self._login_offline())
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        # On fixe explicitement la taille : sinon le dernier bouton se retrouve
        # rogné en bas de la fenêtre selon la police du système.
        self.update_idletasks()
        width = max(420, self.winfo_reqwidth())
        height = self.winfo_reqheight() + 10
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 3)
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.after(120, self._grab)
        self.entry.focus_set()

    def _grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    def _set_message(self, text, color=None):
        self.message.configure(text=text, text_color=color or COLORS["danger"])

    def _set_busy(self, busy):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.ms_btn.configure(state=state)
        self.offline_btn.configure(state=state)
        self.entry.configure(state=state)

    # -- Hors-ligne (crack) ------------------------------------------------
    def _login_offline(self):
        if self._busy:
            return
        try:
            username = validate_username(self.entry.get())
            self.session = login_offline(username)
        except ValueError as e:
            self._set_message(str(e))
            return
        except Exception as e:
            self._set_message(f"Erreur : {e}")
            return
        self._close()

    # -- Microsoft ---------------------------------------------------------
    def _login_microsoft(self):
        if self._busy:
            return
        self._cancel_event.clear()
        self._set_busy(True)
        self._set_message("Ouverture du navigateur…", COLORS["accent"])
        self.cancel_btn.configure(text="Annuler la connexion")

        def status(text):
            self._safe(lambda: self._set_message(text, COLORS["accent"]))

        def run():
            try:
                session = login_microsoft(on_status=status, cancel_event=self._cancel_event)
            except Exception as e:
                message = str(e)
                self._safe(lambda: self._on_ms_error(message))
                return
            self._safe(lambda: self._on_ms_success(session))

        threading.Thread(target=run, daemon=True).start()

    def _on_ms_success(self, session):
        self.session = session
        self._close()

    def _on_ms_error(self, message):
        self._set_busy(False)
        self.cancel_btn.configure(text="Annuler")
        self._set_message(message)

    def _safe(self, callback):
        """Repasse dans le thread Tk sans planter si la fenêtre est déjà fermée."""
        try:
            if self.winfo_exists():
                self.after(0, callback)
        except Exception:
            pass

    # -- Fermeture ---------------------------------------------------------
    def _close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _cancel(self):
        # Si une connexion Microsoft est en cours, on la débloque proprement
        self._cancel_event.set()
        if self._busy:
            self._set_busy(False)
            self.cancel_btn.configure(text="Annuler")
            self._set_message("Connexion annulée.", COLORS["text_mute"])
            return
        self.session = None
        self._close()

    def get_session(self):
        self.wait_window()
        return self.session


class ModsPage(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.mods = []
        self._filter_job = None
        self._build()
        self._load_mods()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))

        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="Mods du pack", font=font(20, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w")
        self.count_label = ctk.CTkLabel(titles, text="Chargement…", font=font(11),
                                        text_color=COLORS["text_mute"])
        self.count_label.pack(anchor="w")

        self.search = ctk.CTkEntry(
            head, width=240, height=34, corner_radius=9, font=font(12),
            placeholder_text="🔍  Rechercher un mod…", fg_color=COLORS["surface"],
            border_color=COLORS["border"], text_color=COLORS["text"],
        )
        self.search.pack(side="right")
        self.search.bind("<KeyRelease>", self._on_search)

        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 16))
        self.scroll_frame.grid_columnconfigure(0, weight=1)

        self.loading_label = ctk.CTkLabel(self.scroll_frame, text="Chargement de la liste…",
                                          text_color=COLORS["text_mute"], font=font(12))
        self.loading_label.pack(pady=20)

    def _load_mods(self):
        def fetch():
            try:
                modpack = fetch_modpack()
                mods = modpack.get("mods", [])
                self.app.ui(lambda: self._display_mods(mods))
            except Exception as e:
                message = str(e)
                self.app.ui(lambda: self._display_error(message))
        threading.Thread(target=fetch, daemon=True).start()

    def _display_mods(self, mods):
        self.mods = sorted(mods, key=lambda m: str(m.get("name", "Inconnu")).lower())
        if self.loading_label is not None:
            self.loading_label.destroy()
            self.loading_label = None
        self.count_label.configure(text=f"{len(self.mods)} mods synchronisés automatiquement")
        self._render(self.mods)

    def _render(self, mods):
        for child in self.scroll_frame.winfo_children():
            child.destroy()

        if not mods:
            ctk.CTkLabel(self.scroll_frame, text="Aucun mod ne correspond.",
                         text_color=COLORS["text_mute"], font=font(12)).pack(pady=20)
            return

        for mod in mods:
            card = ctk.CTkFrame(self.scroll_frame, corner_radius=10, fg_color=COLORS["surface"],
                                border_width=1, border_color=COLORS["border"])
            card.pack(fill="x", pady=2, padx=4)
            ctk.CTkLabel(card, text=str(mod.get("name", "Mod inconnu")), font=font(13, "bold"),
                         text_color=COLORS["text"]).pack(anchor="w", padx=12, pady=(6, 0))
            ctk.CTkLabel(card, text=str(mod.get("filename", "Fichier non spécifié")),
                         font=font(10), text_color=COLORS["text_mute"]).pack(anchor="w", padx=12, pady=(0, 6))

    def _on_search(self, event=None):
        # Anti-rebond : on ne reconstruit pas 74 cartes à chaque touche
        if self._filter_job is not None:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(220, self._apply_filter)

    def _apply_filter(self):
        self._filter_job = None
        query = self.search.get().strip().lower()
        if not query:
            self._render(self.mods)
            return
        self._render([m for m in self.mods
                      if query in str(m.get("name", "")).lower()
                      or query in str(m.get("filename", "")).lower()])

    def _display_error(self, error_msg):
        if self.loading_label is not None:
            self.loading_label.configure(text=f"Erreur de chargement : {error_msg}",
                                         text_color=COLORS["danger"])


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
            return 8  # Sécurité si on ne peut pas lire la RAM (ex: macOS/Linux)

    def _card(self, parent, title, subtitle=None):
        card = ctk.CTkFrame(parent, corner_radius=12, fg_color=COLORS["surface"],
                            border_width=1, border_color=COLORS["border"])
        card.pack(fill="x", padx=18, pady=7)
        ctk.CTkLabel(card, text=title, font=font(14, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=16, pady=(14, 2))
        if subtitle:
            # anchor="w" sur le label lui-même : sans ça le texte est centré dans
            # le widget et se retrouve rogné des deux côtés.
            ctk.CTkLabel(card, text=subtitle, font=font(11), text_color=COLORS["text_mute"],
                         justify="left", anchor="w", wraplength=660).pack(
                             anchor="w", fill="x", padx=16, pady=(0, 8))
        return card

    def _switch(self, parent, text, key, default, command=None):
        switch = ctk.CTkSwitch(
            parent, text=text, font=font(12), text_color=COLORS["text_dim"],
            progress_color=COLORS["accent"], button_color=COLORS["text"],
            command=command or (lambda: self._toggle(key, switch)),
        )
        switch.pack(anchor="w", padx=16, pady=5)
        if self.app.settings.get(key, default):
            switch.select()
        return switch

    def _toggle(self, key, switch):
        self.app.settings[key] = bool(switch.get())
        self.app.save_settings()
        self.app.refresh_side_panel()

    def _build(self):
        ctk.CTkLabel(self, text="Paramètres", font=font(20, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=18, pady=(18, 2))

        self.container = container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=0, pady=(6, 12))

        # --- Mémoire ---
        card = self._card(container, "Mémoire RAM allouée",
                          "Le curseur est limité à 60 % de votre RAM totale. Au-delà de 8 Go, "
                          "le ramasse-miettes de Java devient plus lent : plus de RAM ≠ plus de FPS.")

        current_ram = min(self.app.settings.get("ram", 6), self.max_ram)
        if self.app.settings.get("ram", 6) > self.max_ram:
            self.app.settings["ram"] = current_ram
            self.app.save_settings()

        self.ram_val_label = ctk.CTkLabel(card, text=f"{current_ram} Go", font=font(15, "bold"),
                                          text_color=COLORS["accent"])
        self.ram_val_label.pack(anchor="w", padx=16)

        steps = max(1, self.max_ram - 2)
        self.slider = ctk.CTkSlider(card, from_=2, to=self.max_ram, number_of_steps=steps,
                                    progress_color=COLORS["accent"], button_color=COLORS["accent"],
                                    button_hover_color=COLORS["accent_h"], fg_color=COLORS["surface_2"],
                                    command=self._on_slider_change)
        self.slider.set(current_ram)
        self.slider.pack(fill="x", padx=16, pady=(8, 10))
        ctk.CTkButton(card, text="Valeur recommandée", width=170, height=28, corner_radius=8,
                      font=font(11), fg_color=COLORS["surface_2"], hover_color=COLORS["hover"],
                      text_color=COLORS["text_dim"],
                      command=self._recommend_ram).pack(anchor="w", padx=16, pady=(0, 16))

        # --- Performances en jeu ---
        card2 = self._card(container, "Performances en jeu",
                           "Ces réglages sont appliqués juste avant le lancement. "
                           "« Ne pas modifier » laisse tes options Minecraft intactes.")

        ctk.CTkLabel(card2, text="Profil graphique", font=font(11, "bold"),
                     text_color=COLORS["text_dim"]).pack(anchor="w", padx=16, pady=(4, 4))
        self.profile_seg = ctk.CTkSegmentedButton(
            card2, values=list(PROFILE_LABELS.keys()), font=font(11),
            selected_color=COLORS["accent"], selected_hover_color=COLORS["accent_h"],
            unselected_color=COLORS["surface_2"], unselected_hover_color=COLORS["hover"],
            text_color=COLORS["text"], fg_color=COLORS["surface_2"],
            command=self._on_profile_change,
        )
        current_profile = self.app.settings.get("perf_profile", "none")
        reverse = {v: k for k, v in PROFILE_LABELS.items()}
        self.profile_seg.set(reverse.get(current_profile, "Ne pas modifier"))
        self.profile_seg.pack(anchor="w", fill="x", padx=16, pady=(0, 10))

        self.profile_hint = ctk.CTkLabel(card2, text="", font=font(10),
                                         text_color=COLORS["text_mute"], justify="left",
                                         anchor="w", wraplength=660)
        self.profile_hint.pack(anchor="w", fill="x", padx=16, pady=(0, 10))
        self._update_profile_hint(current_profile)

        ctk.CTkLabel(card2, text="Shaders (Oculus)", font=font(11, "bold"),
                     text_color=COLORS["text_dim"]).pack(anchor="w", padx=16, pady=(4, 4))
        self.shader_seg = ctk.CTkSegmentedButton(
            card2, values=list(SHADER_LABELS.keys()), font=font(11),
            selected_color=COLORS["accent"], selected_hover_color=COLORS["accent_h"],
            unselected_color=COLORS["surface_2"], unselected_hover_color=COLORS["hover"],
            text_color=COLORS["text"], fg_color=COLORS["surface_2"],
            command=self._on_shader_change,
        )
        reverse_shaders = {v: k for k, v in SHADER_LABELS.items()}
        self.shader_seg.set(reverse_shaders.get(self.app.settings.get("shaders", "auto"), "Auto"))
        self.shader_seg.pack(anchor="w", fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(card2, text="Couper les shaders est de loin le plus gros gain de FPS.",
                     font=font(10), text_color=COLORS["text_mute"]).pack(anchor="w", padx=16, pady=(0, 10))

        self.opt_switch = self._switch(card2, "Flags JVM optimisés (recommandé)", "optimized_flags", True)
        self.prio_switch = self._switch(card2, "Donner la priorité CPU au jeu", "high_priority", True)
        self.mini_switch = self._switch(card2, "Mettre le launcher en veille pendant le jeu",
                                        "minimize_on_launch", True)
        ctk.CTkLabel(card2, text="En veille, le launcher se réduit, libère sa mémoire et passe en "
                                 "priorité basse : il ne consomme quasiment plus rien pendant la partie.",
                     font=font(10), text_color=COLORS["text_mute"], justify="left",
                     anchor="w", wraplength=660).pack(anchor="w", fill="x", padx=16, pady=(0, 16))

        # --- Fenêtre de jeu ---
        card3 = self._card(container, "Fenêtre de jeu")
        self.fs_switch = self._switch(card3, "Lancer en plein écran", "fullscreen", False)
        self.close_switch = self._switch(card3, "Fermer le launcher quand le jeu démarre",
                                         "close_on_launch", False)

        ctk.CTkLabel(card3, text="Résolution personnalisée (laisser vide = par défaut)",
                     font=font(11), text_color=COLORS["text_mute"]).pack(anchor="w", padx=16, pady=(12, 4))
        res_row = ctk.CTkFrame(card3, fg_color="transparent")
        res_row.pack(anchor="w", fill="x", padx=16, pady=(0, 16))

        self.w_entry = ctk.CTkEntry(res_row, placeholder_text="Largeur (1920)", width=140, height=32,
                                    corner_radius=8, font=font(12), fg_color=COLORS["surface_2"],
                                    border_color=COLORS["border"], text_color=COLORS["text"])
        self.w_entry.pack(side="left", padx=(0, 8))
        self.w_entry.insert(0, str(self.app.settings.get("res_width", "")))
        self.w_entry.bind("<KeyRelease>", self._save_resolution)

        ctk.CTkLabel(res_row, text="×", text_color=COLORS["text_mute"]).pack(side="left")

        self.h_entry = ctk.CTkEntry(res_row, placeholder_text="Hauteur (1080)", width=140, height=32,
                                    corner_radius=8, font=font(12), fg_color=COLORS["surface_2"],
                                    border_color=COLORS["border"], text_color=COLORS["text"])
        self.h_entry.pack(side="left", padx=8)
        self.h_entry.insert(0, str(self.app.settings.get("res_height", "")))
        self.h_entry.bind("<KeyRelease>", self._save_resolution)

        # --- Maintenance ---
        card4 = self._card(container, "Maintenance",
                           "Réinstalle Minecraft et Forge si le jeu refuse de démarrer ou plante.")
        buttons = ctk.CTkFrame(card4, fg_color="transparent")
        buttons.pack(anchor="w", padx=16, pady=(0, 16))
        ctk.CTkButton(buttons, text="🔧  Réparer / Réinstaller", height=32, corner_radius=8,
                      font=font(12), fg_color=COLORS["danger"], hover_color=COLORS["danger_h"],
                      command=self.app.repair_installation).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="📁  Ouvrir le dossier", height=32, corner_radius=8,
                      font=font(12), fg_color=COLORS["surface_2"], hover_color=COLORS["hover"],
                      text_color=COLORS["text_dim"],
                      command=self.app.open_game_folder).pack(side="left")

    def on_show(self):
        # La page est gardée en mémoire : on la remet en haut à chaque affichage
        try:
            self.container._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    # -- Callbacks ---------------------------------------------------------
    def _on_slider_change(self, value):
        ram = int(value)
        self.ram_val_label.configure(text=f"{ram} Go")
        self.app.settings["ram"] = ram
        self.app.save_settings()
        self.app.refresh_side_panel()

    def _recommend_ram(self):
        # 8 Go couvrent largement ce modpack ; au-delà le GC coûte plus qu'il ne rapporte
        ram = max(4, min(8, self.max_ram))
        self.slider.set(ram)
        self._on_slider_change(ram)

    def _update_profile_hint(self, profile):
        hints = {
            "none": "Aucun réglage graphique ne sera touché.",
            "perf": "Distance 8 · graphismes rapides · particules minimales · ombres et nuages coupés · 120 FPS max.",
            "balanced": "Distance 12 · graphismes rapides · nuages coupés · 144 FPS max.",
            "quality": "Distance 16 · graphismes détaillés · toutes les particules · 260 FPS max.",
        }
        self.profile_hint.configure(text=hints.get(profile, ""))

    def _on_profile_change(self, label):
        profile = PROFILE_LABELS.get(label, "none")
        self.app.settings["perf_profile"] = profile
        self.app.save_settings()
        self._update_profile_hint(profile)
        self.app.refresh_side_panel()

    def _on_shader_change(self, label):
        self.app.settings["shaders"] = SHADER_LABELS.get(label, "auto")
        self.app.save_settings()
        self.app.refresh_side_panel()

    def _save_resolution(self, event=None):
        self.app.settings["res_width"] = self.w_entry.get().strip()
        self.app.settings["res_height"] = self.h_entry.get().strip()
        self.app.save_settings()


class LauncherApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"Community Craft Launcher v{LAUNCHER_VERSION}")
        self.geometry("1164x706")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])
        self._center_window(1164, 706)
        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass

        self.session = None
        self.pages = {}
        self.home_page = None
        self.current_page = None
        self.mod_count = None
        self.game_process = None
        self.minecraft_dir = os.path.join(
            os.getenv("APPDATA") or os.path.expanduser("~"), ".community-craft-v3")
        self.settings = self._load_settings()

        self._build()
        self._auto_connect()
        self._fetch_stats()
        self._check_update()
        self._start_rich_presence()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- Utilitaires -------------------------------------------------------
    def _center_window(self, width, height):
        try:
            x = (self.winfo_screenwidth() - width) // 2
            y = max(0, (self.winfo_screenheight() - height) // 2 - 30)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass

    def ui(self, callback):
        """Exécute un callback dans le thread Tk, sans planter si la fenêtre est fermée."""
        try:
            self.after(0, callback)
        except Exception:
            pass

    def status(self, text, color="gray"):
        if self.home_page is not None:
            self.home_page.set_status(text, color)

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.sidebar = Sidebar(self, self.navigate, self)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")

        self.topbar = TopBar(self)
        self.topbar.grid(row=0, column=1, columnspan=2, sticky="ew")

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.side_panel = SidePanel(self, self)
        self.side_panel.grid(row=1, column=2, sticky="nsew")

        self.navigate("home")

    # -- Réglages ----------------------------------------------------------
    def _settings_path(self):
        return os.path.join(self.minecraft_dir, "launcher_settings.json")

    def _load_settings(self):
        default_settings = {
            "ram": 6,                     # 6 Go par défaut
            "optimized_flags": True,      # Flags JVM optimisés activés par défaut
            "fullscreen": False,
            "close_on_launch": False,
            "minimize_on_launch": True,   # Le launcher s'efface pendant la partie
            "high_priority": True,        # Le jeu passe devant le reste
            "perf_profile": "none",       # Ne touche pas aux options du joueur
            "shaders": "auto",
            "res_width": "",
            "res_height": "",
            "last_launch": 0,
            "jvm_cache": {},
        }
        try:
            with open(self._settings_path(), "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                default_settings.update(loaded)
        except (OSError, ValueError):
            pass

        # On revalide : le fichier est éditable à la main
        try:
            default_settings["ram"] = max(2, min(64, int(default_settings.get("ram", 6))))
        except (TypeError, ValueError):
            default_settings["ram"] = 6
        if default_settings.get("perf_profile") not in PERF_PROFILES:
            default_settings["perf_profile"] = "none"
        if default_settings.get("shaders") not in ("auto", "on", "off"):
            default_settings["shaders"] = "auto"
        if not isinstance(default_settings.get("jvm_cache"), dict):
            default_settings["jvm_cache"] = {}
        return default_settings

    def save_settings(self):
        path = self._settings_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
            os.replace(tmp, path)  # écriture atomique
        except Exception as e:
            print(f"Erreur sauvegarde config : {e}")

    # Ancien nom conservé pour ne rien casser ailleurs
    _save_settings = save_settings

    def refresh_side_panel(self):
        self.side_panel.update_info(self.mod_count)
        if self.home_page is not None:
            self.home_page.update_stats(self.mod_count)

    # -- Session -----------------------------------------------------------
    def _auto_connect(self):
        try:
            session = load_session()
            if session:
                self.session = session
                self._on_connected(session)
        except Exception as e:
            print(f"Erreur auto-connect : {e}")

    def connect_minecraft(self):
        current = self.session["username"] if self.session else ""
        session = LoginDialog(self, current).get_session()
        if not session:
            self.status("Connexion annulée.", "gray")
            return
        self.session = session
        self._on_connected(session)

    # Ancien nom conservé
    _connect_minecraft = connect_minecraft

    def logout(self):
        clear_session()
        self.session = None
        self.side_panel.set_disconnected()
        self.topbar.set_state("● Hors ligne", COLORS["text_mute"])
        if self.home_page is not None:
            self.home_page.hero.set_user("Invité")
            self.home_page.hero.set_playable(False, "CONNEXION REQUISE")
            self.home_page.set_status("Déconnecté.", "gray")

    def _session_state(self):
        """Texte + couleur de l'état de session pour la barre du haut."""
        if not self.session:
            return "● Hors ligne", COLORS["text_mute"]
        if self.session.get("type") == SESSION_MICROSOFT:
            return "● Connecté (Microsoft)", COLORS["accent"]
        return "● Connecté (hors-ligne)", COLORS["warn"]

    def _on_connected(self, session):
        username = session["username"]
        self.side_panel.set_user(username, session.get("type", SESSION_OFFLINE))
        self.topbar.set_state(*self._session_state())
        if self.home_page is not None:
            self.home_page.set_connected(username)
            self.home_page.set_status(f"Bonjour {username} — prêt à jouer.", COLORS["accent"])

    def _fetch_stats(self):
        def fetch():
            try:
                count = len(fetch_modpack().get("mods", []))
            except Exception:
                count = None
            self.ui(lambda: self._apply_stats(count))
        threading.Thread(target=fetch, daemon=True).start()

    def _apply_stats(self, count):
        self.mod_count = count if count is not None else "?"
        self.refresh_side_panel()

    # -- Navigation --------------------------------------------------------
    def navigate(self, page):
        """Affiche une page. Les pages sont gardées en mémoire : pas de
        reconstruction ni de re-téléchargement de la liste à chaque clic."""
        if page == self.current_page:
            return
        if self.current_page is not None and self.current_page in self.pages:
            self.pages[self.current_page].grid_forget()

        if page not in self.pages:
            builders = {"home": HomePage, "mods": ModsPage, "settings": SettingsPage}
            self.pages[page] = builders[page](self.content, self)
            if page == "home":
                self.home_page = self.pages[page]

        self.pages[page].grid(row=0, column=0, sticky="nsew")
        self.current_page = page
        self.sidebar.set_active(page)
        on_show = getattr(self.pages[page], "on_show", None)
        if on_show is not None:
            on_show()

        if page == "home":
            self.home_page.update_stats(self.mod_count)
            self.home_page.refresh_subline()
            if self.session:
                self.home_page.set_connected(self.session["username"])
            else:
                self.home_page.hero.set_playable(False, "CONNEXION REQUISE")
        self.refresh_side_panel()

    # Ancien nom conservé
    _navigate = navigate

    # -- Lancement du jeu --------------------------------------------------
    def launch_game(self):
        if not self.session:
            self.status("Choisis d'abord un pseudo !", COLORS["danger"])
            return
        self.home_page.hero.set_playable(False, "VÉRIFICATION…")
        self.status("Synchronisation des mods…")
        self.home_page.show_progress()

        def on_progress(mod_name, state):
            if state == "downloading":
                self.ui(lambda: self.status(f"Téléchargement : {mod_name}"))
            elif state == "deleting":
                self.ui(lambda: self.status(f"Suppression : {mod_name}"))

        def on_overall(done, total):
            frac = done / total if total else 1
            self.ui(lambda: self.home_page.set_progress(frac))

        def on_complete(error):
            if error:
                self.ui(self.home_page.hide_progress)
                self.ui(lambda: self.status(f"Erreur mods : {error}", COLORS["danger"]))
                self.ui(lambda: self.home_page.hero.set_playable(True))
            else:
                self.ui(self._lancer_minecraft)

        sync_mods(on_progress=on_progress, on_complete=on_complete, on_overall=on_overall)

    # Ancien nom conservé
    _launch_game = launch_game

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
                self.ui(lambda: self.status("Installation de Java…"))
                runtime.install_jvm_runtime(jvm_name, minecraft_dir)
            return runtime.get_executable_path(jvm_name, minecraft_dir)
        except Exception as e:
            print(f"Impossible de préparer le Java de Mojang : {e}")
            return None

    def _supported_jvm_flags(self, java_path, flags):
        """Retire les flags que cette JVM ne connaît pas.

        Un seul flag inconnu et Java refuse de démarrer : on teste donc la liste
        avec « java -version » et on enlève ce qui coince. Le résultat est mis en
        cache pour ne payer ce test qu'une seule fois par machine.
        """
        if not java_path:
            java_path = "java"
        signature = hashlib.sha1(("|".join(flags) + java_path).encode("utf-8")).hexdigest()
        cache = self.settings.get("jvm_cache", {})
        cached = cache.get(signature)
        if isinstance(cached, list):
            return cached

        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = CREATE_NO_WINDOW

        candidate = list(flags)
        for _ in range(8):
            try:
                probe = subprocess.run([java_path] + candidate + ["-version"],
                                       capture_output=True, text=True, errors="ignore",
                                       timeout=30, **kwargs)
            except Exception as e:
                print(f"Test des flags JVM impossible : {e}")
                return []  # Java injoignable : on ne prend aucun risque
            if probe.returncode == 0:
                break
            output = (probe.stderr or "") + (probe.stdout or "")
            # Java se plaint de trois façons différentes selon le type d'erreur
            rejected = re.findall(r"Unrecognized VM option '([^']+)'", output)
            rejected += re.findall(r"Improperly specified VM option '([^'=]+)", output)
            rejected += re.findall(r"Unrecognized option: (\S+)", output)
            if not rejected:
                print(f"Flags JVM refusés sans détail, on repart sur la base : {output[:200]}")
                candidate = []
                break
            before = len(candidate)
            candidate = [f for f in candidate if not self._flag_matches(f, rejected)]
            if len(candidate) == before:
                candidate = []
                break
        else:
            candidate = []

        cache[signature] = candidate
        self.settings["jvm_cache"] = cache
        self.save_settings()
        return candidate

    @staticmethod
    def _flag_matches(flag, rejected_names):
        """« -XX:+UseNUMA » correspond-il à un nom refusé du genre « UseNUMA » ?"""
        name = flag
        if name.startswith("-XX:"):
            name = name[4:].lstrip("+-").split("=")[0]
        return any(name == r or flag == r for r in rejected_names)

    def _build_jvm_args(self, java_path=None):
        """Construit les arguments JVM selon les réglages."""
        ram = self.settings.get("ram", 6)
        if not self.settings.get("optimized_flags", True):
            return [f"-Xmx{ram}G", "-Xms2G"]

        # Xms == Xmx : le tas est alloué une fois pour toutes, pas de
        # redimensionnement (donc pas de à-coups) en pleine partie.
        args = [f"-Xmx{ram}G", f"-Xms{ram}G"]
        flags = self._supported_jvm_flags(java_path, BASE_JVM_FLAGS + EXTRA_JVM_FLAGS)
        if not flags:
            # La sonde n'a rien validé : on garde le strict minimum sans risque
            flags = ["-XX:+UseG1GC", "-Dlog4j2.formatMsgNoLookups=true"]
        return args + flags

    def _apply_game_options(self):
        """Écrit le plein écran et le profil de performance dans options.txt."""
        updates = {"fullscreen": "true" if self.settings.get("fullscreen") else "false"}
        updates.update(PERF_PROFILES.get(self.settings.get("perf_profile", "none"), {}))

        options_path = os.path.join(self.minecraft_dir, "options.txt")
        # Rien à écrire et pas de fichier existant : on ne crée rien
        if not os.path.exists(options_path) and len(updates) == 1 and updates["fullscreen"] == "false":
            return
        try:
            lines = []
            if os.path.exists(options_path):
                with open(options_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.read().splitlines()

            seen = set()
            out = []
            for line in lines:
                key = line.split(":", 1)[0] if ":" in line else None
                if key in updates:
                    out.append(f"{key}:{updates[key]}")
                    seen.add(key)
                else:
                    out.append(line)
            for key, value in updates.items():
                if key not in seen:
                    out.append(f"{key}:{value}")

            tmp = options_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
            os.replace(tmp, options_path)
        except Exception as e:
            print(f"options.txt : {e}")

    # Ancien nom conservé
    _apply_options_txt = _apply_game_options

    def _apply_shader_setting(self):
        mode = self.settings.get("shaders", "auto")
        if mode == "on":
            set_shaders_enabled(True)
        elif mode == "off":
            set_shaders_enabled(False)

    @staticmethod
    def _set_own_priority(priority):
        """Change la priorité du launcher lui-même (Windows uniquement)."""
        if sys.platform != "win32":
            return False
        try:
            lib = _kernel32()
            if not lib.SetPriorityClass(lib.GetCurrentProcess(), priority):
                print(f"Priorité launcher refusée (erreur {ctypes.get_last_error()})")
                return False
            return True
        except Exception as e:
            print(f"Priorité launcher : {e}")
            return False

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
            self.status(f"Impossible d'ouvrir le dossier : {e}", COLORS["danger"])

    def open_log(self):
        """Ouvre le dernier log de lancement."""
        log_path = os.path.join(self.minecraft_dir, "launcher_log.txt")
        if os.path.exists(log_path):
            try:
                os.startfile(log_path)
            except Exception as e:
                self.status(f"Impossible d'ouvrir le log : {e}", COLORS["danger"])
        else:
            self.status("Aucun log pour le moment (lance le jeu d'abord).")

    def open_discord(self):
        if "your-invite" in DISCORD_INVITE:
            self.status("Lien Discord non configuré (voir DISCORD_INVITE dans launcher.py).",
                        COLORS["warn"])
            return
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
            messagebox.showinfo("Réparer", "Installation réinitialisée.\nClique sur « LANCER » pour tout réinstaller.")
        except Exception as e:
            messagebox.showerror("Erreur", f"Réparation impossible : {e}")

    # -- Mise à jour du launcher ------------------------------------------
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
                    url = str(data.get("download_url", ""))
                    sha = str(data.get("sha256", "")).strip().lower()
                    self.ui(lambda: self._prompt_update(latest, url, sha))
            except Exception:
                pass  # Pas de réseau / fichier absent → on ignore silencieusement
        threading.Thread(target=check, daemon=True).start()

    @staticmethod
    def _is_newer(remote, local):
        def parse(v):
            return [int(x) for x in re.findall(r"\d+", v)]
        return parse(remote) > parse(local)

    @staticmethod
    def _check_update_url(url):
        """Une mise à jour, c'est un .exe qu'on va exécuter : on vérifie d'où il vient."""
        parsed = urllib.parse.urlparse(str(url or ""))
        if parsed.scheme != "https":
            raise Exception("le lien de mise à jour n'est pas en HTTPS")
        if (parsed.hostname or "").lower() not in UPDATE_ALLOWED_HOSTS:
            raise Exception(f"hôte de mise à jour non autorisé ({parsed.hostname})")
        return url

    def _prompt_update(self, version, url, expected_sha=""):
        try:
            self._check_update_url(url)
        except Exception as e:
            print(f"Mise à jour ignorée : {e}")
            return

        warning = "" if expected_sha else (
            "\n\n⚠️ Cette mise à jour ne fournit pas d'empreinte sha256 : "
            "son intégrité ne pourra pas être vérifiée."
        )
        if not messagebox.askyesno(
            "Mise à jour disponible",
            f"Une nouvelle version du launcher est disponible (v{version}).\n"
            f"Tu utilises la v{LAUNCHER_VERSION}.\n\nVeux-tu la télécharger maintenant ?" + warning
        ):
            return
        # Si l'URL pointe vers l'exe ET qu'on tourne en .exe -> mise à jour automatique.
        if url.lower().split("?")[0].endswith(".exe") and getattr(sys, "frozen", False):
            self._perform_self_update(url, expected_sha)
        else:
            webbrowser.open(url)

    def _perform_self_update(self, url, expected_sha=""):
        """Télécharge le nouvel exe, le vérifie, le remplace, puis relance."""
        self.status("Téléchargement de la mise à jour…", COLORS["accent"])

        def run():
            new_path = None
            try:
                self._check_update_url(url)
                exe_path = sys.executable
                exe_dir = os.path.dirname(exe_path)
                new_path = os.path.join(exe_dir, "update_tmp.exe")

                digest = hashlib.sha256()
                with requests.get(url, stream=True, timeout=180,
                                  headers={"User-Agent": "CommunityCraft-Launcher/3.2"}) as r:
                    r.raise_for_status()
                    with open(new_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            if chunk:
                                digest.update(chunk)
                                f.write(chunk)

                # Contrôles avant de remplacer l'exe en cours d'exécution
                if expected_sha and digest.hexdigest() != expected_sha:
                    raise Exception("empreinte sha256 incorrecte (fichier altéré ?)")
                with open(new_path, "rb") as f:
                    if f.read(2) != b"MZ":
                        raise Exception("le fichier téléchargé n'est pas un exécutable Windows")

                # Script qui attend la fermeture du launcher, remplace l'exe et le relance
                bat_path = os.path.join(exe_dir, "_update.bat")
                with open(bat_path, "w", encoding="utf-8") as f:
                    f.write(
                        "@echo off\r\n"
                        "timeout /t 2 /nobreak >nul\r\n"
                        f'del "{exe_path}"\r\n'
                        f'move "{new_path}" "{exe_path}"\r\n'
                        f'start "" "{exe_path}"\r\n'
                        'del "%~f0"\r\n'
                    )
                kwargs = {}
                if sys.platform == "win32":
                    kwargs["creationflags"] = CREATE_NO_WINDOW
                subprocess.Popen(["cmd", "/c", bat_path], **kwargs)
                self.ui(self.destroy)
            except Exception as e:
                if new_path and os.path.exists(new_path):
                    try:
                        os.remove(new_path)
                    except OSError:
                        pass
                self.ui(lambda: messagebox.showerror(
                    "Mise à jour",
                    f"Échec de la mise à jour automatique : {e}\nOuverture de la page de téléchargement."))
                self.ui(lambda: webbrowser.open(url))

        threading.Thread(target=run, daemon=True).start()

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

    # -- Surveillance du jeu ----------------------------------------------
    def _monitor_game(self, proc, log_path):
        """Guette un crash au démarrage, puis attend passivement la fin du jeu."""
        def monitor():
            crashed = False
            deadline = time.time() + 24
            while time.time() < deadline:
                code = proc.poll()
                if code is not None:
                    crashed = code != 0
                    break
                time.sleep(2)  # sondage très espacé : zéro CPU volé au jeu

            if crashed:
                tail = self._read_log_tail(log_path)
                self.ui(lambda: self._on_game_crashed(tail))
                return

            self.ui(self._on_game_started)
            proc.wait()  # attente bloquante : ne consomme rien
            self.ui(self._on_game_closed)

        threading.Thread(target=monitor, daemon=True).start()

    def _on_game_crashed(self, tail):
        self._restore_launcher()
        self.status("❌ Le jeu a crashé au démarrage (voir le log).", COLORS["danger"])
        self.home_page.hero.set_playable(True)
        messagebox.showerror(
            "Le jeu a crashé",
            "Minecraft s'est fermé juste après le lancement.\n\n"
            "Dernières lignes du log :\n\n" + tail
        )

    def _on_game_launched(self):
        """Appelé dès que le process est lancé : le launcher s'efface tout de suite.

        C'est le chargement de Minecraft (les 30 premières secondes) qui est le
        plus lourd : autant lui laisser la machine immédiatement.
        """
        self.topbar.set_state("● En jeu", COLORS["ok"])
        self.settings["last_launch"] = int(time.time())
        self.save_settings()

        if not self.settings.get("minimize_on_launch", True):
            return

        # On libère les pages lourdes (la liste des mods, ~75 cartes) et on
        # rend la mémoire à l'OS, puis on passe en priorité basse.
        for name in ("mods", "settings"):
            page = self.pages.pop(name, None)
            if page is not None:
                if self.current_page == name:
                    self.current_page = None
                page.destroy()
        if self.current_page is None:
            self.navigate("home")
        gc.collect()
        # BELOW_NORMAL plutôt qu'IDLE : même effet en jeu (un launcher au repos
        # ne demande aucun CPU) mais l'interface reste utilisable si le joueur
        # revient dessus en alt-tab.
        self._set_own_priority(PRIORITY_BELOW_NORMAL)
        self.iconify()

    def _on_game_started(self):
        """Le jeu a passé la fenêtre de détection de crash : tout va bien."""
        self.status("✅ Jeu lancé — bon jeu !", COLORS["accent"])
        # On ne ferme qu'ici : si le jeu avait crashé, le joueur aurait vu l'erreur
        if self.settings.get("close_on_launch"):
            self.destroy()

    def _on_game_closed(self):
        self._restore_launcher()
        self.status("Partie terminée. À la prochaine !")
        self.home_page.hero.set_playable(True)
        self.home_page.refresh_subline()

    def _restore_launcher(self):
        self.game_process = None
        self._set_own_priority(PRIORITY_NORMAL)
        self.topbar.set_state(*self._session_state())
        try:
            if self.state() == "iconic":
                self.deiconify()
        except Exception:
            pass

    def _on_close(self):
        self._set_own_priority(PRIORITY_NORMAL)
        self.destroy()

    # -- Lancement effectif -------------------------------------------------
    def _lancer_minecraft(self):
        self.status("Préparation du lancement…", COLORS["accent"])
        self.home_page.hero.set_playable(False, "LANCEMENT…")
        self.home_page.show_progress()

        def launch():
            log_file = None
            try:
                # Contrôle d'intégrité des mods avant de lancer
                corrupt = validate_mods()
                if corrupt:
                    apercu = ", ".join(corrupt[:5]) + ("…" if len(corrupt) > 5 else "")
                    self.ui(self.home_page.hide_progress)
                    self.ui(lambda: self.status(
                        f"⚠️ {len(corrupt)} mod(s) corrompu(s). Utilise « Réparer ».", COLORS["danger"]))
                    self.ui(lambda: self.home_page.hero.set_playable(True))
                    self.ui(lambda: messagebox.showwarning(
                        "Mods corrompus",
                        f"{len(corrupt)} fichier(s) de mod sont corrompus :\n\n{apercu}\n\n"
                        "Va dans Paramètres → Maintenance → « Réparer / Réinstaller », "
                        "ou relance pour les retélécharger."))
                    return

                # Le dossier où le jeu sera installé (isolé du vrai .minecraft pour éviter les conflits)
                minecraft_dir = self.minecraft_dir
                forge_version = f"{MC_VERSION}-{FORGE_VERSION}"
                version_id = f"{MC_VERSION}-forge-{FORGE_VERSION}"
                version_dir = os.path.join(minecraft_dir, "versions", version_id)

                # Callback de progression branché sur la barre (setMax / setProgress / setStatus)
                progress_state = {"max": 1}

                def cb_status(text):
                    self.ui(lambda: self.status(text))

                def cb_max(value):
                    progress_state["max"] = max(1, value)

                def cb_progress(value):
                    frac = value / progress_state["max"]
                    self.ui(lambda: self.home_page.set_progress(frac))

                callback = {"setStatus": cb_status, "setProgress": cb_progress, "setMax": cb_max}

                # Ne réinstalle Forge que si le dossier n'existe pas
                if not os.path.exists(version_dir):
                    self.ui(lambda: self.status("Installation de Minecraft (1/2)…"))
                    # Installer Vanilla (télécharge aussi le Java 17 fourni par Mojang)
                    minecraft_launcher_lib.install.install_minecraft_version(
                        MC_VERSION, minecraft_dir, callback=callback)

                    # Récupère le Java de Mojang pour ne PAS dépendre d'un Java installé sur la machine.
                    java_path = self._ensure_java(minecraft_dir)

                    self.ui(lambda: self.status("Installation de Forge (2/2)…"))
                    # Installer Forge en lui imposant le Java de Mojang
                    minecraft_launcher_lib.forge.install_forge_version(
                        forge_version, minecraft_dir, callback=callback, java=java_path)
                else:
                    self.ui(lambda: self.status("Vérification des fichiers…"))
                    java_path = self._ensure_java(minecraft_dir)

                # Compte Microsoft : le jeton d'accès ne vit que quelques heures,
                # on le renouvelle avant chaque partie sinon le jeu refuse la session.
                if self.session.get("type") == SESSION_MICROSOFT:
                    self.ui(lambda: self.status("Vérification du compte Microsoft…"))
                    try:
                        self.session = refresh_microsoft(self.session)
                        self.ui(lambda: self._on_connected(self.session))
                    except Exception as e:
                        message = str(e)
                        self.ui(self.home_page.hide_progress)
                        self.ui(lambda: self.status(message, COLORS["danger"]))
                        self.ui(lambda: self.home_page.hero.set_playable(True))
                        self.ui(lambda: messagebox.showwarning(
                            "Compte Microsoft",
                            f"{message}\n\nClique sur « Changer de compte » pour te reconnecter."))
                        return

                # Réglages appliqués juste avant le lancement
                self.ui(lambda: self.status("Application des réglages de performance…"))
                self._apply_game_options()
                self._apply_shader_setting()

                options = {
                    "username": self.session["username"],
                    "uuid": self.session["uuid"],
                    "token": self.session["access_token"],
                    "jvmArguments": self._build_jvm_args(java_path),
                }
                # Force le jeu à utiliser le Java de Mojang plutôt qu'un Java système incompatible
                if java_path:
                    options["executablePath"] = java_path

                # Résolution personnalisée
                rw = str(self.settings.get("res_width", "")).strip()
                rh = str(self.settings.get("res_height", "")).strip()
                if rw.isdigit() and rh.isdigit():
                    options["customResolution"] = True
                    options["resolutionWidth"] = rw
                    options["resolutionHeight"] = rh

                self.ui(self.home_page.hide_progress)
                self.ui(lambda: self.status("Démarrage du jeu…", COLORS["accent"]))

                # Génère la commande Java et lance le jeu en arrière-plan
                command = minecraft_launcher_lib.command.get_minecraft_command(
                    version_id, minecraft_dir, options)

                # Écrit les erreurs dans un fichier texte pour qu'on puisse les lire tranquillement
                log_path = os.path.join(minecraft_dir, "launcher_log.txt")
                log_file = open(log_path, "w", encoding="utf-8", errors="ignore")

                kwargs = {}
                if sys.platform == "win32":
                    flags = CREATE_NO_WINDOW
                    if self.settings.get("high_priority", True):
                        # Le jeu passe devant les autres applications
                        flags |= PRIORITY_ABOVE_NORMAL
                    kwargs["creationflags"] = flags

                proc = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, cwd=minecraft_dir, **kwargs)
                self.game_process = proc
                self.ui(lambda: self.status("Jeu lancé ! Vérification du démarrage…", COLORS["accent"]))
                # On rend la main au jeu immédiatement (mise en veille du launcher)
                self.ui(self._on_game_launched)

                # Surveille le process pour détecter un crash au démarrage
                self._monitor_game(proc, log_path)

            except Exception as e:
                message = str(e)
                self.ui(self.home_page.hide_progress)
                self.ui(lambda: self.status(f"Erreur : {message}", COLORS["danger"]))
                self.ui(lambda: self.home_page.hero.set_playable(True))
            finally:
                # Le process a hérité du descripteur : on peut refermer le nôtre
                if log_file is not None:
                    try:
                        log_file.close()
                    except Exception:
                        pass

        threading.Thread(target=launch, daemon=True).start()


if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()
