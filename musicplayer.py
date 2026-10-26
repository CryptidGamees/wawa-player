"""
Wawa Player (Windows)
Tabs: Library (playlists + local files), Search (YouTube), Settings.
First launch downloads yt-dlp, Deno and libmpv automatically.

Install once:  pip install PySide6 python-mpv mutagen
Run:           python musicplayer.py
"""
import os
import sys
import json
import time
import random
import shutil
import locale
import ctypes
import zipfile
import tempfile
import subprocess
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QPoint, QUrl
from PySide6.QtGui import (QColor, QPainter, QLinearGradient, QRadialGradient,
                           QPixmap, QPainterPath, QFont, QPalette, QDesktopServices, QIcon)
from PySide6.QtWidgets import (
    QApplication, QWidget, QDialog, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QListWidget, QListWidgetItem, QSlider, QComboBox,
    QProgressBar, QMessageBox, QStackedWidget, QButtonGroup, QMenu,
    QFileDialog, QInputDialog, QCheckBox, QScrollArea,
)

# ================================================================ paths
APP_NAME = "PersonalMusicPlayer"
DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
BIN_DIR = DATA_DIR / "bin"
BIN_DIR.mkdir(parents=True, exist_ok=True)

YTDLP = BIN_DIR / "yt-dlp.exe"
DENO = BIN_DIR / "deno.exe"
MPV_DLL = BIN_DIR / "mpv-2.dll"
LIB_FILE = DATA_DIR / "library.json"
CFG_FILE = DATA_DIR / "config.json"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
UA = {"User-Agent": "PersonalMusicPlayer/2.0"}

os.environ["PATH"] = str(BIN_DIR) + os.pathsep + os.environ.get("PATH", "")
if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(str(BIN_DIR))


def resource_path(name):
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name
    return Path(__file__).resolve().parent / name


# ================================================================ config + library
DEFAULT_CFG = {"volume": 70, "autoplay": True, "quality": "best",
               "glass_blur": False, "shuffle": False,
               "theme": "light", "language": "en"}


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


class Library:
    def __init__(self):
        data = load_json(LIB_FILE, {})
        self.tracks = data.get("tracks", {})        # id -> track dict
        self.playlists = data.get("playlists", {})  # name -> [track ids]

    def save(self):
        save_json(LIB_FILE, {"tracks": self.tracks, "playlists": self.playlists})

    def add_track(self, t):
        self.tracks[t["id"]] = t

    def tracks_in(self, playlist=None):
        if playlist is None:
            return sorted(self.tracks.values(), key=lambda t: t["title"].lower())
        return [self.tracks[i] for i in self.playlists.get(playlist, []) if i in self.tracks]

    def remove_track(self, tid):
        self.tracks.pop(tid, None)
        for ids in self.playlists.values():
            while tid in ids:
                ids.remove(tid)


# ================================================================ downloading deps
def retry(fn, tries=40, delay=0.5):
    """Retry when Windows/antivirus still has a file open (WinError 32)."""
    for i in range(tries):
        try:
            return fn()
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(delay)


def download(url, dest, progress=None):
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(int(done * 100 / total))
    retry(lambda: part.replace(dest))


def install_ytdlp(progress=None):
    download("https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
             YTDLP, progress)


def install_deno(progress=None):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        z = Path(tmp) / "deno.zip"
        download("https://github.com/denoland/deno/releases/latest/download/"
                 "deno-x86_64-pc-windows-msvc.zip", z, progress)
        with zipfile.ZipFile(z) as zf:
            zf.extract("deno.exe", BIN_DIR)


def install_libmpv(progress=None):
    api = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
    with urllib.request.urlopen(urllib.request.Request(api, headers=UA), timeout=30) as r:
        release = json.load(r)
    asset = next((a for a in release["assets"]
                  if a["name"].startswith("mpv-dev-x86_64-")
                  and "-v3-" not in a["name"]
                  and a["name"].endswith(".7z")), None)
    if asset is None:
        raise RuntimeError("Could not find a libmpv download on GitHub.")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        archive = tmp / "mpv.7z"
        seven = tmp / "7zr.exe"
        # 7zr.exe is the tiny official 7-Zip extractor (handles the BCJ2 filter)
        download("https://www.7-zip.org/a/7zr.exe", seven)
        download(asset["browser_download_url"], archive, progress)
        out = tmp / "out"
        r = subprocess.run([str(seven), "x", str(archive), f"-o{out}", "-y"],
                           capture_output=True, text=True,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                           timeout=300)
        if r.returncode != 0:
            raise RuntimeError("Extraction failed: " + (r.stderr or r.stdout)[-200:])
        dll = next((p for p in out.rglob("*.dll")
                    if p.name.lower() in ("libmpv-2.dll", "mpv-2.dll")), None)
        if dll is None:
            raise RuntimeError("libmpv DLL not found in the downloaded archive.")
        retry(lambda: shutil.copy2(dll, MPV_DLL))


DEPENDENCIES = [
    ("yt-dlp (YouTube access)", YTDLP, install_ytdlp),
    ("Deno (YouTube helper)", DENO, install_deno),
    ("libmpv (audio engine)", MPV_DLL, install_libmpv),
]


def ytdlp_version():
    try:
        p = subprocess.run([str(YTDLP), "--version"], capture_output=True,
                           text=True, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, timeout=20)
        return p.stdout.strip() or "unknown"
    except Exception:
        return "not installed"


def update_ytdlp():
    p = subprocess.run([str(YTDLP), "-U"], capture_output=True, text=True,
                       creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, timeout=180)
    return (p.stdout + p.stderr).strip()[-300:] or "Done."


def search_youtube(query):
    p = subprocess.run(
        [str(YTDLP), f"ytsearch20:{query}", "--flat-playlist",
         "--dump-json", "--no-warnings"],
        capture_output=True, text=True, encoding="utf-8",
        creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, timeout=90)
    if p.returncode != 0 and not p.stdout:
        raise RuntimeError(p.stderr.strip()[-300:] or "Search failed")
    out = []
    for line in p.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not d.get("id"):
            continue
        out.append({
            "id": "yt:" + d["id"], "kind": "yt", "vid": d["id"],
            "title": d.get("title") or "Unknown",
            "artist": d.get("channel") or d.get("uploader") or "",
            "duration": d.get("duration") or 0,
        })
    return out


# ================================================================ audio files
def read_audio_meta(path):
    title, artist, dur = Path(path).stem, "", 0
    try:
        import mutagen
        f = mutagen.File(path, easy=True)
        if f is not None:
            if f.info:
                dur = f.info.length
            title = (f.get("title") or [title])[0]
            artist = (f.get("artist") or [""])[0]
    except Exception:
        pass
    return {"id": "file:" + str(path), "kind": "file", "path": str(path),
            "title": title, "artist": artist, "duration": dur}


def read_cover(path):
    try:
        from mutagen.id3 import ID3
        pics = ID3(path).getall("APIC")
        if pics:
            return pics[0].data
    except Exception:
        pass
    return None


def fetch_bytes(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return r.read()


def fmt(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def rounded_cover(data, size, radius=24):
    pm = QPixmap()
    if data:
        pm.loadFromData(data)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    p.setClipPath(path)
    if pm.isNull():
        g = QLinearGradient(0, 0, size, size)
        g.setColorAt(0, QColor(110, 90, 200))
        g.setColorAt(1, QColor(210, 70, 120))
        p.fillRect(0, 0, size, size, g)
        p.setPen(QColor(255, 255, 255, 210))
        f = QFont()
        f.setPixelSize(size // 3)
        p.setFont(f)
        p.drawText(out.rect(), Qt.AlignCenter, "\u266A")
    else:
        s = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawPixmap((size - s.width()) // 2, (size - s.height()) // 2, s)
    p.end()
    return out


# ================================================================ Windows blur
def apply_backdrop(hwnd):
    """Windows 11 acrylic blur behind the window. Silently does nothing on failure."""
    try:
        dwm = ctypes.windll.dwmapi
        h = ctypes.c_void_p(hwnd)

        class MARGINS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int),
                        ("t", ctypes.c_int), ("b", ctypes.c_int)]

        dwm.DwmSetWindowAttribute(h, 20, ctypes.byref(ctypes.c_int(1)), 4)   # dark mode
        dwm.DwmExtendFrameIntoClientArea(h, ctypes.byref(MARGINS(-1, -1, -1, -1)))
        dwm.DwmSetWindowAttribute(h, 38, ctypes.byref(ctypes.c_int(3)), 4)   # acrylic
    except Exception:
        pass


def apply_blur_win10(hwnd):
    """Experimental blur-behind for Windows 10 (undocumented Windows API)."""
    try:
        class ACCENT(ctypes.Structure):
            _fields_ = [("state", ctypes.c_int), ("flags", ctypes.c_int),
                        ("color", ctypes.c_uint), ("anim", ctypes.c_int)]

        class DATA(ctypes.Structure):
            _fields_ = [("attr", ctypes.c_int), ("data", ctypes.c_void_p),
                        ("size", ctypes.c_size_t)]

        accent = ACCENT(4, 2, 0x33FFFFFF, 0)  # acrylic blur, tint is AABBGGRR
        data = DATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                    ctypes.sizeof(accent))
        ctypes.windll.user32.SetWindowCompositionAttribute(
            ctypes.c_void_p(hwnd), ctypes.byref(data))
    except Exception:
        pass


# ================================================================ threads
class Task(QThread):
    ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.ok.emit(self.fn())
        except Exception as e:
            self.failed.emit(str(e))


class SetupWorker(QThread):
    status = Signal(str)
    progress = Signal(int)
    done = Signal()
    failed = Signal(str)

    def run(self):
        try:
            for name, path, installer in DEPENDENCIES:
                if path.exists():
                    continue
                self.status.emit(tr("Downloading {name}...").format(name=tr(name)))
                self.progress.emit(0)
                installer(self.progress.emit)
            self.progress.emit(100)
            self.done.emit()
        except Exception as e:
            self.failed.emit(str(e))


class SetupDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("First-time setup"))
        self.setMinimumWidth(440)
        self.label = QLabel(tr("Preparing..."))
        self.bar = QProgressBar()
        self.retry = QPushButton(tr("Retry"))
        self.retry.hide()
        self.retry.clicked.connect(self.start)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        lay.addWidget(QLabel(tr("<b>Setting things up</b><br>This only happens once.")))
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        lay.addWidget(self.retry)
        QTimer.singleShot(0, self.start)

    def start(self):
        self.retry.hide()
        self.worker = SetupWorker()
        self.worker.status.connect(self.label.setText)
        self.worker.progress.connect(self.bar.setValue)
        self.worker.done.connect(self.accept)
        self.worker.failed.connect(self.on_fail)
        self.worker.start()

    def on_fail(self, msg):
        self.label.setText(tr("Setup failed: {msg}\nCheck your internet and retry.").format(msg=msg))
        self.retry.show()


# ================================================================ style
# ================================================================ languages
LANG = {"code": "en"}

PT = {
    "First-time setup": "Configuração inicial",
    "<b>Setting things up</b><br>This only happens once.": "<b>Preparando tudo</b><br>Isso só acontece uma vez.",
    "Preparing...": "Preparando...",
    "Retry": "Tentar novamente",
    "Downloading {name}...": "Baixando {name}...",
    "yt-dlp (YouTube access)": "yt-dlp (acesso ao YouTube)",
    "Deno (YouTube helper)": "Deno (auxiliar do YouTube)",
    "libmpv (audio engine)": "libmpv (motor de áudio)",
    "Setup failed: {msg}\nCheck your internet and retry.": "A configuração falhou: {msg}\nVerifique sua internet e tente novamente.",
    "Library": "Biblioteca",
    "Search": "Buscar",
    "Settings": "Configurações",
    "Nothing playing": "Nada tocando",
    "Vol": "Vol",
    "Click to change the play order": "Clique para mudar a ordem de reprodução",
    "Shuffle": "Aleatório",
    "In order": "Em ordem",
    "+ Playlist": "+ Playlist",
    "Delete": "Excluir",
    "+ Add music files": "+ Adicionar músicas",
    "Click a song to play it. Right-click for more options.": "Clique em uma música para tocar. Clique com o botão direito para mais opções.",
    "All songs": "Todas as músicas",
    "Empty. Add music files or save songs from Search.": "Vazio. Adicione músicas ou salve músicas da aba Buscar.",
    "Search YouTube...": "Buscar no YouTube...",
    "Add selected to playlist": "Adicionar selecionadas à playlist",
    "Save to library": "Salvar na biblioteca",
    "Searching...": "Buscando...",
    "No results.": "Nenhum resultado.",
    "Error: {e}": "Erro: {e}",
    "Components": "Componentes",
    "yt-dlp version: ...": "Versão do yt-dlp: ...",
    "yt-dlp version: {v}": "Versão do yt-dlp: {v}",
    "installed": "instalado",
    "missing": "ausente",
    "Update yt-dlp": "Atualizar yt-dlp",
    "Re-download Deno": "Baixar o Deno novamente",
    "Updating yt-dlp...": "Atualizando o yt-dlp...",
    "Downloading Deno...": "Baixando o Deno...",
    "Deno updated.": "Deno atualizado.",
    "Appearance": "Aparência",
    "Theme": "Tema",
    "Dark": "Escuro",
    "Light": "Claro",
    "Language": "Idioma",
    "Restarting...": "Reiniciando...",
    "Preferences": "Preferências",
    "YouTube audio quality": "Qualidade do áudio do YouTube",
    "Best": "Melhor",
    "Data saver": "Economia de dados",
    "Play the next song automatically": "Tocar a próxima música automaticamente",
    "Blur window background (experimental, restart needed)": "Desfocar o fundo da janela (experimental, precisa reiniciar)",
    "Data": "Dados",
    "Saved in: {path}": "Salvo em: {path}",
    "Open data folder": "Abrir pasta de dados",
    "Restart the app to apply this.": "Reinicie o app para aplicar.",
    "Add music": "Adicionar músicas",
    "Audio files (*.mp3 *.flac *.m4a *.wav *.ogg *.opus *.aac);;All files (*.*)": "Arquivos de áudio (*.mp3 *.flac *.m4a *.wav *.ogg *.opus *.aac);;Todos os arquivos (*.*)",
    "Added {n} file(s).": "{n} arquivo(s) adicionado(s).",
    "New playlist": "Nova playlist",
    "Playlist name:": "Nome da playlist:",
    "Delete playlist": "Excluir playlist",
    "Delete '{pl}'? Your songs stay in the library.": "Excluir '{pl}'? Suas músicas continuam na biblioteca.",
    "Added {n} song(s) to '{name}'.": "{n} música(s) adicionada(s) a '{name}'.",
    "Saved {n} song(s) to your library.": "{n} música(s) salva(s) na biblioteca.",
    "New playlist...": "Nova playlist...",
    "Play": "Tocar",
    "Add to playlist": "Adicionar à playlist",
    "Remove from this playlist": "Remover desta playlist",
    "Remove from library": "Remover da biblioteca",
    "Select one or more results first.": "Selecione um ou mais resultados primeiro.",
    "File not found. It may have been moved.": "Arquivo não encontrado. Ele pode ter sido movido.",
    "Loading from YouTube...": "Carregando do YouTube...",
    "Couldn't play this. Try Settings -> Update yt-dlp.": "Não foi possível tocar. Tente Configurações -> Atualizar yt-dlp.",
    "Startup error": "Erro ao iniciar",
    "Could not start the audio engine:\n{e}": "Não foi possível iniciar o motor de áudio:\n{e}",
    "Yes": "Sim",
    "No": "Não",
    "OK": "OK",
    "Cancel": "Cancelar",
}


def tr(text):
    if LANG["code"] == "pt":
        return PT.get(text, text)
    return text


# ================================================================ themes
THEMES = {
    "dark": dict(
        text="#f4f6ff", dim="rgba(255,255,255,170)", faint="rgba(255,255,255,125)",
        dialog="#10163a",
        panel="rgba(5,9,30,150)", panel_border="rgba(255,255,255,55)",
        pill="rgba(255,255,255,18)", pill_border="rgba(255,255,255,32)",
        ctl="rgba(255,255,255,24)", ctl_border="rgba(255,255,255,42)",
        ctl_hover="rgba(255,255,255,46)", ctl_press="rgba(255,255,255,70)",
        tab_on="rgba(255,255,255,72)", hover="rgba(255,255,255,24)",
        sel="rgba(255,255,255,58)", big="rgba(255,255,255,60)",
        groove="rgba(255,255,255,55)", fill="rgba(255,255,255,225)", handle="#ffffff",
        menu="#141a42", menu_border="rgba(255,255,255,55)", menu_sel="rgba(255,255,255,45)",
        grad=((76, 116, 240), (10, 22, 78)),
        pal=dict(window=(16, 22, 58), text=(244, 246, 255), base=(24, 32, 72),
                 button=(36, 46, 96), hl=(84, 118, 238), hltext=(255, 255, 255)),
    ),
    "light": dict(
        text="#14213d", dim="rgba(20,33,61,165)", faint="rgba(20,33,61,125)",
        dialog="#eef2ff",
        panel="rgba(255,255,255,125)", panel_border="rgba(255,255,255,190)",
        pill="rgba(255,255,255,90)", pill_border="rgba(255,255,255,160)",
        ctl="rgba(255,255,255,115)", ctl_border="rgba(20,33,61,45)",
        ctl_hover="rgba(255,255,255,175)", ctl_press="rgba(255,255,255,215)",
        tab_on="rgba(255,255,255,205)", hover="rgba(255,255,255,95)",
        sel="rgba(70,100,215,70)", big="rgba(255,255,255,205)",
        groove="rgba(20,33,61,45)", fill="rgba(40,80,210,235)", handle="#2a50d2",
        menu="#f4f7ff", menu_border="rgba(20,33,61,60)", menu_sel="rgba(70,100,215,55)",
        grad=((84, 124, 240), (242, 246, 255)),
        pal=dict(window=(238, 242, 255), text=(20, 33, 61), base=(255, 255, 255),
                 button=(224, 231, 250), hl=(84, 118, 238), hltext=(255, 255, 255)),
    ),
}

STYLE_TEMPLATE = """
QWidget { color: @text@; font-family: 'Segoe UI Variable Text','Segoe UI'; font-size: 13px; }
QDialog, QMessageBox, QInputDialog { background: @dialog@; }
QFrame#glass { background: @panel@; border: 1px solid @panel_border@; border-radius: 24px; }
QFrame#pill { background: @pill@; border: 1px solid @pill_border@; border-radius: 20px; }
QLabel#dim { color: @dim@; }
QLabel#small { color: @faint@; font-size: 11px; }
QLineEdit { background: @ctl@; border: 1px solid @ctl_border@; border-radius: 16px;
            padding: 8px 14px; selection-background-color: @sel@; }
QPushButton { background: @ctl@; border: 1px solid @ctl_border@; border-radius: 16px; padding: 7px 16px; }
QPushButton:hover { background: @ctl_hover@; }
QPushButton:pressed { background: @ctl_press@; }
QPushButton#tab { background: transparent; border: none; border-radius: 16px;
                  padding: 8px 20px; color: @dim@; }
QPushButton#tab:checked { background: @tab_on@; color: @text@; }
QPushButton#icon { font-size: 20px; background: transparent; border: none; padding: 6px 16px; }
QPushButton#icon:hover { background: @hover@; }
QPushButton#big { font-size: 22px; background: @big@; padding: 6px 20px; }
QComboBox { background: @ctl@; border: 1px solid @ctl_border@; border-radius: 16px; padding: 7px 14px; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: @menu@; border: 1px solid @menu_border@;
                              selection-background-color: @menu_sel@; selection-color: @text@; outline: 0; }
QListWidget { background: transparent; border: none; outline: 0; }
QListWidget::item { padding: 8px 10px; border-radius: 14px; }
QListWidget::item:hover { background: @hover@; }
QListWidget::item:selected { background: @sel@; color: @text@; }
QSlider::groove:horizontal { height: 4px; background: @groove@; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @fill@; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; height: 12px; margin: -4px 0; background: @handle@; border-radius: 6px; }
QMenu { background: @menu@; border: 1px solid @menu_border@; border-radius: 12px; padding: 6px; }
QMenu::item { padding: 7px 20px; border-radius: 8px; }
QMenu::item:selected { background: @menu_sel@; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: @groove@; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QProgressBar { background: @groove@; border: none; border-radius: 6px; height: 12px;
               text-align: center; color: @text@; }
QProgressBar::chunk { background: @fill@; border-radius: 6px; }
"""


def build_style(t):
    css = STYLE_TEMPLATE
    for k, v in t.items():
        if isinstance(v, str):
            css = css.replace("@" + k + "@", v)
    return css


def apply_theme(app, name):
    t = THEMES.get(name, THEMES["light"])
    p = t["pal"]
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(*p["window"]))
    pal.setColor(QPalette.WindowText, QColor(*p["text"]))
    pal.setColor(QPalette.Base, QColor(*p["base"]))
    pal.setColor(QPalette.AlternateBase, QColor(*p["window"]))
    pal.setColor(QPalette.Text, QColor(*p["text"]))
    pal.setColor(QPalette.Button, QColor(*p["button"]))
    pal.setColor(QPalette.ButtonText, QColor(*p["text"]))
    pal.setColor(QPalette.Highlight, QColor(*p["hl"]))
    pal.setColor(QPalette.HighlightedText, QColor(*p["hltext"]))
    pal.setColor(QPalette.PlaceholderText, QColor(p["text"][0], p["text"][1], p["text"][2], 130))
    app.setPalette(pal)
    app.setStyleSheet(build_style(t))


def ask_yes_no(parent, title, text):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(tr("Yes"), QMessageBox.YesRole)
    box.addButton(tr("No"), QMessageBox.NoRole)
    box.exec()
    return box.buttonRole(box.clickedButton()) == QMessageBox.YesRole


def ask_text(parent, title, label):
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setOkButtonText(tr("OK"))
    dlg.setCancelButtonText(tr("Cancel"))
    ok = dlg.exec()
    return dlg.textValue().strip(), bool(ok)


class LanguageDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wawa Player")
        self.setMinimumWidth(380)
        self.choice = "en"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        lay.addWidget(QLabel("<b>Choose your language / Escolha o idioma</b>"))
        for code, name in (("en", "English"), ("pt", "Português")):
            b = QPushButton(tr(name))
            b.clicked.connect(lambda checked=False, c=code: self.pick(c))
            lay.addWidget(b)

    def pick(self, code):
        self.choice = code
        self.accept()


def add_action(menu, text, fn):
    a = menu.addAction(text)
    a.triggered.connect(lambda checked=False: fn())
    return a


def tracks_of(lw):
    out = []
    for i in range(lw.count()):
        t = lw.item(i).data(Qt.UserRole)
        if t:
            out.append(t)
    return out


def make_item(t):
    parts = [t.get("artist") or "", fmt(t["duration"]) if t.get("duration") else ""]
    sub = "  \u00B7  ".join(p for p in parts if p)
    it = QListWidgetItem(f"{t['title']}\n{sub}" if sub else t["title"])
    it.setData(Qt.UserRole, t)
    return it


def glass():
    f = QFrame()
    f.setObjectName("glass")
    return f


# ================================================================ main window
class TrackList(QListWidget):
    """List that can play a song with a single left click."""
    clicked_item = Signal(object)

    def __init__(self, single_click=False):
        super().__init__()
        self.single_click = single_click
        self._press = None

    def mousePressEvent(self, e):
        self._press = self.itemAt(e.position().toPoint())
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        multi = bool(e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier))
        if self.single_click and e.button() == Qt.LeftButton and not multi:
            it = self.itemAt(e.position().toPoint())
            if it is not None and it is self._press and it.data(Qt.UserRole):
                self.clicked_item.emit(it)


class Bridge(QObject):
    position = Signal(float)
    duration = Signal(float)
    paused = Signal(bool)
    idle = Signal(bool)
    eof = Signal(bool)


class MainWindow(QWidget):
    COVER = 300

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wawa Player")
        self.resize(1000, 660)
        self.setMinimumSize(840, 560)

        self.cfg = {**DEFAULT_CFG, **load_json(CFG_FILE, {})}
        self.lib = Library()
        self.tasks = []
        self.queue, self.qi, self.current = [], -1, None
        self.history, self.played_ids, self.ended = [], set(), False
        self.dur, self.seeking, self.started, self.got_dur = 0.0, False, False, False

        self.win_build = sys.getwindowsversion().build if hasattr(sys, "getwindowsversion") else 0
        self.acrylic = bool(self.cfg["glass_blur"]) and self.win_build >= 17134
        if self.acrylic:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._backdrop_done = False

        self.init_player()
        self.build_ui()
        self.refresh_playlists()
        self.refresh_versions()

    # ---------------------------------------------------------- engine
    def init_player(self):
        import mpv
        locale.setlocale(locale.LC_NUMERIC, "C")
        fmt_str = "bestaudio/best" if self.cfg["quality"] == "best" else "worstaudio/worst"
        self.player = mpv.MPV(
            vid="no", ytdl=True, ytdl_format=fmt_str,
            script_opts=f"ytdl_hook-ytdl_path={YTDLP.as_posix()}",
            input_default_bindings=False, input_vo_keyboard=False, osc=False,
            keep_open="yes", audio_display="no", demuxer_max_bytes="32MiB", demuxer_max_back_bytes="8MiB")
        self.player.volume = self.cfg["volume"]
        self.bridge = Bridge()
        self.bridge.position.connect(self.on_pos)
        self.bridge.duration.connect(self.on_dur)
        self.bridge.paused.connect(self.on_paused)
        self.bridge.idle.connect(self.on_idle)
        self.bridge.eof.connect(self.on_eof)
        self.player.observe_property("time-pos", lambda n, v: v is not None and self.bridge.position.emit(float(v)))
        self.player.observe_property("duration", lambda n, v: v is not None and self.bridge.duration.emit(float(v)))
        self.player.observe_property("pause", lambda n, v: self.bridge.paused.emit(bool(v)))
        self.player.observe_property("idle-active", lambda n, v: self.bridge.idle.emit(bool(v)))
        self.player.observe_property("eof-reached", lambda n, v: self.bridge.eof.emit(bool(v)))

    # ---------------------------------------------------------- UI
    def build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(16)

        # ---- left panel
        left = glass()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(18, 18, 18, 14)
        ll.setSpacing(12)

        pill = QFrame()
        pill.setObjectName("pill")
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(5, 5, 5, 5)
        pl.setSpacing(2)
        self.stack = QStackedWidget()
        group = QButtonGroup(self)
        group.setExclusive(True)
        for i, name in enumerate(("Library", "Search", "Settings")):
            b = QPushButton(tr(name))
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setChecked(i == 0)
            group.addButton(b)
            b.clicked.connect(lambda checked=False, i=i: self.stack.setCurrentIndex(i))
            pl.addWidget(b)
        tabrow = QHBoxLayout()
        tabrow.addStretch()
        tabrow.addWidget(pill)
        tabrow.addStretch()
        ll.addLayout(tabrow)

        self.stack.addWidget(self.build_library_page())
        self.stack.addWidget(self.build_search_page())
        self.stack.addWidget(self.build_settings_page())
        ll.addWidget(self.stack, 1)

        self.status = QLabel("")
        self.status.setObjectName("dim")
        ll.addWidget(self.status)
        root.addWidget(left, 1)

        # ---- right panel (now playing)
        right = glass()
        right.setFixedWidth(372)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(24, 24, 24, 20)
        rl.setSpacing(6)
        self.cover = QLabel()
        self.cover.setFixedSize(self.COVER, self.COVER)
        self.cover.setPixmap(rounded_cover(None, self.COVER))
        rl.addWidget(self.cover, 0, Qt.AlignHCenter)
        rl.addSpacing(10)
        self.title_lbl = QLabel(tr("Nothing playing"))
        self.title_lbl.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.title_lbl.setAlignment(Qt.AlignHCenter)
        self.title_lbl.setWordWrap(True)
        self.artist_lbl = QLabel("")
        self.artist_lbl.setObjectName("dim")
        self.artist_lbl.setAlignment(Qt.AlignHCenter)
        self.artist_lbl.setWordWrap(True)
        rl.addWidget(self.title_lbl)
        rl.addWidget(self.artist_lbl)
        rl.addSpacing(8)

        self.seek = QSlider(Qt.Horizontal)
        self.seek.sliderPressed.connect(lambda: setattr(self, "seeking", True))
        self.seek.sliderReleased.connect(self.do_seek)
        rl.addWidget(self.seek)
        times = QHBoxLayout()
        self.t_left, self.t_right = QLabel("0:00"), QLabel("0:00")
        for l in (self.t_left, self.t_right):
            l.setObjectName("small")
        times.addWidget(self.t_left)
        times.addStretch()
        times.addWidget(self.t_right)
        rl.addLayout(times)

        ctr = QHBoxLayout()
        prev = QPushButton("|\u25C0")
        prev.setObjectName("icon")
        prev.clicked.connect(self.play_prev)
        self.play_btn = QPushButton("\u25B6")
        self.play_btn.setObjectName("big")
        self.play_btn.clicked.connect(self.toggle_pause)
        nxt = QPushButton("\u25B6|")
        nxt.setObjectName("icon")
        nxt.clicked.connect(self.play_next)
        ctr.addStretch()
        ctr.addWidget(prev)
        ctr.addWidget(self.play_btn)
        ctr.addWidget(nxt)
        ctr.addStretch()
        rl.addLayout(ctr)
        rl.addStretch()

        vrow = QHBoxLayout()
        self.order_btn = QPushButton()
        self.order_btn.setToolTip(tr("Click to change the play order"))
        self.order_btn.clicked.connect(self.toggle_order)
        self.update_order_btn()
        vrow.addWidget(self.order_btn)
        vrow.addSpacing(10)
        vrow.addWidget(QLabel(tr("Vol")))
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(self.cfg["volume"])
        self.vol.valueChanged.connect(self.on_volume)
        self.vol.sliderReleased.connect(self.save_cfg)
        vrow.addWidget(self.vol)
        rl.addLayout(vrow)
        root.addWidget(right)

    # ---- Library page
    def build_library_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        row = QHBoxLayout()
        self.pl_combo = QComboBox()
        self.pl_combo.currentIndexChanged.connect(lambda _: self.refresh_library_list())
        new = QPushButton(tr("+ Playlist"))
        new.clicked.connect(lambda: self.new_playlist())
        self.del_pl_btn = QPushButton(tr("Delete"))
        self.del_pl_btn.clicked.connect(self.delete_playlist)
        row.addWidget(self.pl_combo, 1)
        row.addWidget(new)
        row.addWidget(self.del_pl_btn)
        lay.addLayout(row)
        add = QPushButton(tr("+ Add music files"))
        add.clicked.connect(self.add_files)
        lay.addWidget(add)
        self.lib_list = TrackList(single_click=True)
        self.lib_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.lib_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lib_list.customContextMenuRequested.connect(lambda pos: self.track_menu(self.lib_list, pos, "library"))
        self.lib_list.clicked_item.connect(lambda it: self.play_item(self.lib_list, it))
        lay.addWidget(self.lib_list, 1)
        hint = QLabel(tr("Click a song to play it. Right-click for more options."))
        hint.setObjectName("small")
        lay.addWidget(hint)
        return w

    # ---- Search page
    def build_search_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Search YouTube..."))
        self.search_box.returnPressed.connect(self.do_search)
        go = QPushButton(tr("Search"))
        go.clicked.connect(self.do_search)
        row.addWidget(self.search_box, 1)
        row.addWidget(go)
        lay.addLayout(row)
        self.res_list = QListWidget()
        self.res_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.res_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.res_list.customContextMenuRequested.connect(lambda pos: self.track_menu(self.res_list, pos, "search"))
        self.res_list.itemDoubleClicked.connect(lambda it: self.play_item(self.res_list, it))
        lay.addWidget(self.res_list, 1)
        brow = QHBoxLayout()
        self.add_pl_btn = QPushButton(tr("Add selected to playlist"))
        self.add_pl_btn.clicked.connect(self.search_add_menu)
        save = QPushButton(tr("Save to library"))
        save.clicked.connect(lambda: self.save_tracks(self.selected_tracks(self.res_list)))
        brow.addWidget(self.add_pl_btn)
        brow.addWidget(save)
        lay.addLayout(brow)
        return w

    # ---- Settings page
    def build_settings_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 0)
        lay.setSpacing(10)

        def head(text):
            l = QLabel(text)
            l.setStyleSheet("font-size: 15px; font-weight: 600; margin-top: 8px;")
            return l

        lay.addWidget(head(tr("Components")))
        self.ver_lbl = QLabel(tr("yt-dlp version: ..."))
        lay.addWidget(self.ver_lbl)
        lay.addWidget(QLabel("libmpv: " + tr("installed" if MPV_DLL.exists() else "missing")))
        lay.addWidget(QLabel("Deno: " + tr("installed" if DENO.exists() else "missing")))
        brow = QHBoxLayout()
        b1 = QPushButton(tr("Update yt-dlp"))
        b1.clicked.connect(lambda: self.run_task(update_ytdlp, tr("Updating yt-dlp..."), refresh=True))
        b2 = QPushButton(tr("Re-download Deno"))
        b2.clicked.connect(lambda: self.run_task(lambda: (install_deno(), tr("Deno updated."))[1], tr("Downloading Deno...")))
        brow.addWidget(b1)
        brow.addWidget(b2)
        brow.addStretch()
        lay.addLayout(brow)
        self.set_info = QLabel("")
        self.set_info.setWordWrap(True)
        self.set_info.setObjectName("dim")
        lay.addWidget(self.set_info)

        lay.addWidget(head(tr("Appearance")))
        trow = QHBoxLayout()
        trow.addWidget(QLabel(tr("Theme")))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems([tr("Dark"), tr("Light")])
        self.theme_combo.setCurrentIndex(0 if self.cfg["theme"] == "dark" else 1)
        self.theme_combo.currentIndexChanged.connect(self.on_theme)
        trow.addWidget(self.theme_combo)
        trow.addStretch()
        lay.addLayout(trow)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel(tr("Language")))
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["English", "Português"])
        self.lang_combo.setCurrentIndex(1 if self.cfg["language"] == "pt" else 0)
        self.lang_combo.currentIndexChanged.connect(self.on_language)
        lrow.addWidget(self.lang_combo)
        lrow.addStretch()
        lay.addLayout(lrow)

        lay.addWidget(head(tr("Preferences")))
        qrow = QHBoxLayout()
        qrow.addWidget(QLabel(tr("YouTube audio quality")))
        self.q_combo = QComboBox()
        self.q_combo.addItems([tr("Best"), tr("Data saver")])
        self.q_combo.setCurrentIndex(0 if self.cfg["quality"] == "best" else 1)
        self.q_combo.currentIndexChanged.connect(self.on_quality)
        qrow.addWidget(self.q_combo)
        qrow.addStretch()
        lay.addLayout(qrow)
        self.auto_chk = QCheckBox(tr("Play the next song automatically"))
        self.auto_chk.setChecked(self.cfg["autoplay"])
        self.auto_chk.toggled.connect(self.on_autoplay)
        lay.addWidget(self.auto_chk)
        self.blur_chk = QCheckBox(tr("Blur window background (experimental, restart needed)"))
        self.blur_chk.setChecked(self.cfg["glass_blur"])
        self.blur_chk.toggled.connect(self.on_blur)
        lay.addWidget(self.blur_chk)

        lay.addWidget(head(tr("Data")))
        lay.addWidget(QLabel(tr("Saved in: {path}").format(path=DATA_DIR)))
        op = QPushButton(tr("Open data folder"))
        op.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR))))
        orow = QHBoxLayout()
        orow.addWidget(op)
        orow.addStretch()
        lay.addLayout(orow)
        lay.addStretch()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(w)
        sa.viewport().setAutoFillBackground(False)
        w.setAutoFillBackground(False)
        return sa

    # ---------------------------------------------------------- background
    def paintEvent(self, e):
        p = QPainter(self)
        a = 205 if self.acrylic else 255
        g = QLinearGradient(0, 0, self.width(), self.height())
        c1, c2 = THEMES.get(self.cfg.get("theme"), THEMES["light"])["grad"]
        g.setColorAt(0.0, QColor(c1[0], c1[1], c1[2], a))
        g.setColorAt(1.0, QColor(c2[0], c2[1], c2[2], a))
        p.fillRect(self.rect(), g)

    def showEvent(self, e):
        super().showEvent(e)
        if self.acrylic and not self._backdrop_done:
            self._backdrop_done = True
            if self.win_build >= 22621:
                apply_backdrop(int(self.winId()))
            else:
                apply_blur_win10(int(self.winId()))

    # ---------------------------------------------------------- helpers
    def toast(self, text):
        self.status.setText(text)
        QTimer.singleShot(3500, lambda: self.status.setText(""))

    def run_task(self, fn, label, refresh=False):
        self.set_info.setText(label)
        t = Task(fn)
        t.ok.connect(lambda r: (self.set_info.setText(str(r)), refresh and self.refresh_versions()))
        t.failed.connect(lambda e: self.set_info.setText(tr("Error: {e}").format(e=e)))
        self.tasks.append(t)
        t.start()

    def refresh_versions(self):
        t = Task(ytdlp_version)
        t.ok.connect(lambda v: self.ver_lbl.setText(tr("yt-dlp version: {v}").format(v=v)))
        self.tasks.append(t)
        t.start()

    def save_cfg(self):
        try:
            save_json(CFG_FILE, self.cfg)
        except Exception:
            pass

    def on_volume(self, v):
        self.player.volume = v
        self.cfg["volume"] = v

    def on_quality(self, i):
        self.cfg["quality"] = "best" if i == 0 else "saver"
        try:
            self.player.ytdl_format = "bestaudio/best" if i == 0 else "worstaudio/worst"
        except Exception:
            pass
        self.save_cfg()

    def on_autoplay(self, on):
        self.cfg["autoplay"] = on
        self.save_cfg()

    def on_blur(self, on):
        self.cfg["glass_blur"] = on
        self.save_cfg()
        self.set_info.setText(tr("Restart the app to apply this."))

    def on_theme(self, i):
        self.cfg["theme"] = "dark" if i == 0 else "light"
        apply_theme(QApplication.instance(), self.cfg["theme"])
        self.save_cfg()
        self.update()

    def on_language(self, i):
        code = "pt" if i == 1 else "en"
        if code == self.cfg["language"]:
            return
        self.cfg["language"] = code
        self.save_cfg()
        LANG["code"] = code
        self.set_info.setText(tr("Restarting..."))
        QTimer.singleShot(700, self.restart_app)

    def restart_app(self):
        try:
            self.player.terminate()
        except Exception:
            pass
        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable], close_fds=True)
            else:
                subprocess.Popen([sys.executable, os.path.abspath(sys.argv[0])], close_fds=True)
        except Exception:
            pass
        os._exit(0)

    # ---------------------------------------------------------- library actions
    def current_playlist(self):
        i = self.pl_combo.currentIndex()
        return None if i <= 0 else self.pl_combo.currentText()

    def refresh_playlists(self, select=None):
        self.pl_combo.blockSignals(True)
        self.pl_combo.clear()
        self.pl_combo.addItem(tr("All songs"))
        self.pl_combo.addItems(list(self.lib.playlists.keys()))
        if select and select in self.lib.playlists:
            self.pl_combo.setCurrentText(select)
        self.pl_combo.blockSignals(False)
        self.refresh_library_list()

    def refresh_library_list(self):
        pl = self.current_playlist()
        self.del_pl_btn.setEnabled(pl is not None)
        self.lib_list.clear()
        tracks = self.lib.tracks_in(pl)
        if not tracks:
            self.lib_list.addItem(tr("Empty. Add music files or save songs from Search."))
        for t in tracks:
            self.lib_list.addItem(make_item(t))

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, tr("Add music"), str(Path.home() / "Music"),
            tr("Audio files (*.mp3 *.flac *.m4a *.wav *.ogg *.opus *.aac);;All files (*.*)"))
        if not files:
            return
        pl = self.current_playlist()
        for f in files:
            t = read_audio_meta(f)
            self.lib.add_track(t)
            if pl and t["id"] not in self.lib.playlists[pl]:
                self.lib.playlists[pl].append(t["id"])
        self.lib.save()
        self.refresh_library_list()
        self.toast(tr("Added {n} file(s).").format(n=len(files)))

    def new_playlist(self, add=None):
        name, ok = ask_text(self, tr("New playlist"), tr("Playlist name:"))
        if not ok or not name:
            return
        if name not in self.lib.playlists:
            self.lib.playlists[name] = []
            self.lib.save()
        if add:
            self.add_to_playlist(name, add)
        self.refresh_playlists(select=name)

    def delete_playlist(self):
        pl = self.current_playlist()
        if not pl:
            return
        if ask_yes_no(self, tr("Delete playlist"),
                      tr("Delete '{pl}'? Your songs stay in the library.").format(pl=pl)):
            self.lib.playlists.pop(pl, None)
            self.lib.save()
            self.refresh_playlists()

    def add_to_playlist(self, name, tracks):
        for t in tracks:
            self.lib.add_track(t)
            if t["id"] not in self.lib.playlists[name]:
                self.lib.playlists[name].append(t["id"])
        self.lib.save()
        if self.current_playlist() in (None, name):
            self.refresh_library_list()
        self.toast(tr("Added {n} song(s) to '{name}'.").format(n=len(tracks), name=name))

    def save_tracks(self, tracks):
        if not tracks:
            return
        for t in tracks:
            self.lib.add_track(t)
        self.lib.save()
        self.refresh_library_list()
        self.toast(tr("Saved {n} song(s) to your library.").format(n=len(tracks)))

    def fill_playlist_menu(self, menu, tracks):
        for name in self.lib.playlists:
            add_action(menu, name, lambda n=name: self.add_to_playlist(n, tracks))
        if self.lib.playlists:
            menu.addSeparator()
        add_action(menu, tr("New playlist..."), lambda: self.new_playlist(add=tracks))

    def selected_tracks(self, lw):
        return [i.data(Qt.UserRole) for i in lw.selectedItems() if i.data(Qt.UserRole)]

    def track_menu(self, lw, pos, where):
        items = [i for i in lw.selectedItems() if i.data(Qt.UserRole)]
        if not items:
            return
        tracks = [i.data(Qt.UserRole) for i in items]
        m = QMenu(self)
        add_action(m, tr("Play"), lambda: self.play_item(lw, items[0]))
        self.fill_playlist_menu(m.addMenu(tr("Add to playlist")), tracks)
        if where == "library":
            pl = self.current_playlist()
            if pl:
                add_action(m, tr("Remove from this playlist"), lambda: self.remove_from_playlist(pl, tracks))
            add_action(m, tr("Remove from library"), lambda: self.remove_from_library(tracks))
        else:
            add_action(m, tr("Save to library"), lambda: self.save_tracks(tracks))
        m.exec(lw.viewport().mapToGlobal(pos))

    def remove_from_playlist(self, pl, tracks):
        for t in tracks:
            if t["id"] in self.lib.playlists[pl]:
                self.lib.playlists[pl].remove(t["id"])
        self.lib.save()
        self.refresh_library_list()

    def remove_from_library(self, tracks):
        for t in tracks:
            self.lib.remove_track(t["id"])
        self.lib.save()
        self.refresh_library_list()

    def search_add_menu(self):
        tracks = self.selected_tracks(self.res_list)
        if not tracks:
            self.toast(tr("Select one or more results first."))
            return
        m = QMenu(self)
        self.fill_playlist_menu(m, tracks)
        m.exec(self.add_pl_btn.mapToGlobal(QPoint(0, -m.sizeHint().height())))

    # ---------------------------------------------------------- search
    def do_search(self):
        q = self.search_box.text().strip()
        if not q:
            return
        self.res_list.clear()
        self.res_list.addItem(tr("Searching..."))
        t = Task(lambda: search_youtube(q))
        t.ok.connect(self.show_results)
        t.failed.connect(lambda e: (self.res_list.clear(), self.res_list.addItem(tr("Error: {e}").format(e=e))))
        self.tasks.append(t)
        t.start()

    def show_results(self, tracks):
        self.res_list.clear()
        if not tracks:
            self.res_list.addItem(tr("No results."))
        for t in tracks:
            self.res_list.addItem(make_item(t))

    # ---------------------------------------------------------- playback
    def play_item(self, lw, item):
        if not item.data(Qt.UserRole):
            return
        self.queue = tracks_of(lw)
        self.qi = lw.row(item)
        self.history = []
        self.played_ids = set()
        self.play_current()

    def play_current(self):
        t = self.queue[self.qi]
        if t["kind"] == "file":
            src = t["path"]
            if not os.path.exists(src):
                self.artist_lbl.setText(tr("File not found. It may have been moved."))
                return
        else:
            src = f"https://www.youtube.com/watch?v={t['vid']}"
        self.current = t
        self.ended = False
        self.got_dur = False
        self.played_ids.add(t["id"])
        self.title_lbl.setText(t["title"])
        self.artist_lbl.setText(tr("Loading from YouTube...") if t["kind"] == "yt"
                                else (t.get("artist") or ""))
        self.seek.setValue(0)
        self.cover.setPixmap(rounded_cover(None, self.COVER))
        self.player.pause = False
        self.player.play(src)
        self.load_cover(t)

    def load_cover(self, t):
        def work():
            if t["kind"] == "file":
                return read_cover(t["path"])
            for q in ("maxresdefault", "mqdefault"):
                try:
                    return fetch_bytes(f"https://i.ytimg.com/vi/{t['vid']}/{q}.jpg")
                except Exception:
                    pass
            return None
        task = Task(work)
        tid = t["id"]
        task.ok.connect(lambda data: self.current and self.current["id"] == tid and data
                        and self.cover.setPixmap(rounded_cover(data, self.COVER)))
        self.tasks.append(task)
        task.start()

    def pick_next_index(self):
        n = len(self.queue)
        if n == 0:
            return None
        if not self.cfg["shuffle"]:
            return (self.qi + 1) % n          # in order, wraps to the first song
        fresh = [i for i, t in enumerate(self.queue) if t["id"] not in self.played_ids]
        if not fresh:                          # everything played: start a new round
            self.played_ids = {self.current["id"]} if self.current else set()
            fresh = [i for i, t in enumerate(self.queue) if t["id"] not in self.played_ids]
        return random.choice(fresh) if fresh else self.qi

    def play_next(self):
        idx = self.pick_next_index()
        if idx is None:
            return
        if self.qi >= 0:
            self.history.append(self.qi)
            del self.history[:-200]
        self.qi = idx
        self.play_current()

    def play_prev(self):
        if not self.queue:
            return
        try:
            pos = self.player.time_pos or 0
        except Exception:
            pos = 0
        if self.ended:
            self.play_current()
        elif pos > 3 or not self.history:
            self.player.seek(0, reference="absolute")
        else:
            self.qi = self.history.pop()
            self.play_current()

    def toggle_order(self):
        self.cfg["shuffle"] = not self.cfg["shuffle"]
        self.played_ids = {self.current["id"]} if self.current else set()
        self.update_order_btn()
        self.save_cfg()

    def update_order_btn(self):
        self.order_btn.setText("\u21C4  " + tr("Shuffle") if self.cfg["shuffle"] else "\u2192  " + tr("In order"))

    def toggle_pause(self):
        if not self.current:
            return
        if self.ended:
            self.play_current()
            return
        try:
            if self.player.idle_active:
                self.play_current()
                return
        except Exception:
            pass
        self.player.pause = not self.player.pause

    def finish_track(self):
        if self.ended or not self.current:
            return
        self.ended = True
        if self.cfg["autoplay"] and self.queue:
            self.play_next()
        else:
            self.seek.setValue(0)
            self.t_left.setText("0:00")
            self.play_btn.setText("\u25B6")

    def do_seek(self):
        self.ended = False
        self.player.seek(self.seek.value(), reference="absolute")
        self.seeking = False

    # ---------------------------------------------------------- mpv events
    def on_pos(self, t):
        if not self.seeking:
            self.seek.setValue(int(t))
        self.t_left.setText(fmt(t))

    def on_dur(self, d):
        self.dur = d
        self.got_dur = True
        self.seek.setRange(0, int(d))
        self.t_right.setText(fmt(d))
        if self.current:
            self.artist_lbl.setText(self.current.get("artist") or "")

    def on_paused(self, p):
        self.play_btn.setText("\u25B6" if p else "\u275A\u275A")

    def on_eof(self, reached):
        if reached:
            self.finish_track()

    def on_idle(self, idle):
        if not idle:
            self.started = True
            return
        if not self.started:
            return
        self.started = False
        if not self.got_dur:
            self.artist_lbl.setText(tr("Couldn't play this. Try Settings -> Update yt-dlp."))
        else:
            self.finish_track()

    def closeEvent(self, e):
        self.save_cfg()
        try:
            self.player.terminate()
        except Exception:
            pass
        super().closeEvent(e)


# ================================================================ entry point
def main():
    app = QApplication(sys.argv)
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WawaPlayer.App")
    except Exception:
        pass
    icon = resource_path("icon.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    app.setStyle("Fusion")
    saved = load_json(CFG_FILE, {})
    cfg = {**DEFAULT_CFG, **saved}
    apply_theme(app, cfg["theme"])
    if "language" not in saved:
        dlg = LanguageDialog()
        dlg.exec()
        cfg["language"] = dlg.choice
        save_json(CFG_FILE, {**saved, "language": dlg.choice})
    LANG["code"] = cfg["language"] if cfg["language"] in ("en", "pt") else "en"

    if not all(path.exists() for _, path, _ in DEPENDENCIES):
        if SetupDialog().exec() != QDialog.Accepted:
            os._exit(0)
    try:
        win = MainWindow()
    except Exception as e:
        QMessageBox.critical(None, tr("Startup error"), tr("Could not start the audio engine:\n{e}").format(e=e))
        return
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
