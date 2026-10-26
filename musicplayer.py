"""
Glass Music Player (Windows)
Tabs: Library (playlists + local files), Search (YouTube), Settings.
First launch downloads yt-dlp, Deno and libmpv automatically.

Install once:  pip install PySide6 python-mpv py7zr mutagen
Run:           python musicplayer.py
"""
import os
import sys
import json
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
                           QPixmap, QPainterPath, QFont, QPalette, QDesktopServices)
from PySide6.QtWidgets import (
    QApplication, QWidget, QDialog, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QListWidget, QListWidgetItem, QSlider, QComboBox,
    QProgressBar, QMessageBox, QStackedWidget, QButtonGroup, QMenu,
    QFileDialog, QInputDialog, QCheckBox,
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


# ================================================================ config + library
DEFAULT_CFG = {"volume": 70, "autoplay": True, "quality": "best", "blur": True}


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
    part.replace(dest)


def install_ytdlp(progress=None):
    download("https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
             YTDLP, progress)


def install_deno(progress=None):
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / "deno.zip"
        download("https://github.com/denoland/deno/releases/latest/download/"
                 "deno-x86_64-pc-windows-msvc.zip", z, progress)
        with zipfile.ZipFile(z) as zf:
            zf.extract("deno.exe", BIN_DIR)


def install_libmpv(progress=None):
    import py7zr
    api = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
    with urllib.request.urlopen(urllib.request.Request(api, headers=UA), timeout=30) as r:
        release = json.load(r)
    asset = next((a for a in release["assets"]
                  if a["name"].startswith("mpv-dev-x86_64-")
                  and "-v3-" not in a["name"]
                  and a["name"].endswith(".7z")), None)
    if asset is None:
        raise RuntimeError("Could not find a libmpv download on GitHub.")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "mpv.7z"
        download(asset["browser_download_url"], archive, progress)
        out = Path(tmp) / "out"
        with py7zr.SevenZipFile(archive) as z:
            z.extractall(out)
        dll = next((p for p in out.rglob("*.dll")
                    if p.name.lower() in ("libmpv-2.dll", "mpv-2.dll")), None)
        if dll is None:
            raise RuntimeError("libmpv DLL not found in the downloaded archive.")
        shutil.copy2(dll, MPV_DLL)


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
                self.status.emit(f"Downloading {name}...")
                self.progress.emit(0)
                installer(self.progress.emit)
            self.progress.emit(100)
            self.done.emit()
        except Exception as e:
            self.failed.emit(str(e))


class SetupDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("First-time setup")
        self.setMinimumWidth(440)
        self.label = QLabel("Preparing...")
        self.bar = QProgressBar()
        self.retry = QPushButton("Retry")
        self.retry.hide()
        self.retry.clicked.connect(self.start)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        lay.addWidget(QLabel("<b>Setting things up</b><br>This only happens once."))
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
        self.label.setText(f"Setup failed: {msg}\nCheck your internet and retry.")
        self.retry.show()


# ================================================================ style
STYLE = """
QWidget { color: #f4f3fa; font-family: 'Segoe UI Variable Text','Segoe UI'; font-size: 13px; }
QDialog, QMessageBox, QInputDialog { background: #1d1a30; }
QFrame#glass { background: rgba(255,255,255,20); border: 1px solid rgba(255,255,255,42); border-radius: 24px; }
QFrame#pill { background: rgba(255,255,255,16); border: 1px solid rgba(255,255,255,30); border-radius: 20px; }
QLineEdit { background: rgba(255,255,255,24); border: 1px solid rgba(255,255,255,40);
            border-radius: 16px; padding: 8px 14px; selection-background-color: rgba(255,255,255,80); }
QPushButton { background: rgba(255,255,255,24); border: 1px solid rgba(255,255,255,42);
              border-radius: 16px; padding: 7px 16px; }
QPushButton:hover { background: rgba(255,255,255,46); }
QPushButton:pressed { background: rgba(255,255,255,70); }
QPushButton#tab { background: transparent; border: none; border-radius: 16px;
                  padding: 8px 20px; color: rgba(255,255,255,165); }
QPushButton#tab:checked { background: rgba(255,255,255,72); color: white; }
QPushButton#icon { font-size: 20px; background: transparent; border: none; padding: 6px 16px; }
QPushButton#icon:hover { background: rgba(255,255,255,30); }
QPushButton#big { font-size: 22px; background: rgba(255,255,255,60); padding: 6px 20px; }
QComboBox { background: rgba(255,255,255,24); border: 1px solid rgba(255,255,255,42);
            border-radius: 16px; padding: 7px 14px; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: #2a2540; border: 1px solid rgba(255,255,255,50);
                              selection-background-color: rgba(255,255,255,50); outline: 0; }
QListWidget { background: transparent; border: none; outline: 0; }
QListWidget::item { padding: 8px 10px; border-radius: 14px; }
QListWidget::item:hover { background: rgba(255,255,255,24); }
QListWidget::item:selected { background: rgba(255,255,255,58); color: white; }
QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,55); border-radius: 2px; }
QSlider::sub-page:horizontal { background: rgba(255,255,255,225); border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; height: 12px; margin: -4px 0;
                             background: white; border-radius: 6px; }
QMenu { background: #2a2540; border: 1px solid rgba(255,255,255,55); border-radius: 12px; padding: 6px; }
QMenu::item { padding: 7px 20px; border-radius: 8px; }
QMenu::item:selected { background: rgba(255,255,255,45); }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,70); border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QProgressBar { background: rgba(255,255,255,30); border: none; border-radius: 6px;
               height: 12px; text-align: center; }
QProgressBar::chunk { background: rgba(255,255,255,210); border-radius: 6px; }
"""


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
class Bridge(QObject):
    position = Signal(float)
    duration = Signal(float)
    paused = Signal(bool)
    idle = Signal(bool)


class MainWindow(QWidget):
    COVER = 300

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Glass Music Player")
        self.resize(1000, 660)
        self.setMinimumSize(840, 560)

        self.cfg = {**DEFAULT_CFG, **load_json(CFG_FILE, {})}
        self.lib = Library()
        self.tasks = []
        self.queue, self.qi, self.current = [], -1, None
        self.dur, self.seeking, self.started, self.got_dur = 0.0, False, False, False

        build = sys.getwindowsversion().build if hasattr(sys, "getwindowsversion") else 0
        self.acrylic = bool(self.cfg["blur"]) and build >= 22621
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
            input_default_bindings=False, input_vo_keyboard=False, osc=False)
        self.player.volume = self.cfg["volume"]
        self.bridge = Bridge()
        self.bridge.position.connect(self.on_pos)
        self.bridge.duration.connect(self.on_dur)
        self.bridge.paused.connect(self.on_paused)
        self.bridge.idle.connect(self.on_idle)
        self.player.observe_property("time-pos", lambda n, v: v is not None and self.bridge.position.emit(float(v)))
        self.player.observe_property("duration", lambda n, v: v is not None and self.bridge.duration.emit(float(v)))
        self.player.observe_property("pause", lambda n, v: self.bridge.paused.emit(bool(v)))
        self.player.observe_property("idle-active", lambda n, v: self.bridge.idle.emit(bool(v)))

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
            b = QPushButton(name)
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
        self.status.setStyleSheet("color: rgba(255,255,255,170);")
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
        self.title_lbl = QLabel("Nothing playing")
        self.title_lbl.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.title_lbl.setAlignment(Qt.AlignHCenter)
        self.title_lbl.setWordWrap(True)
        self.artist_lbl = QLabel("")
        self.artist_lbl.setStyleSheet("color: rgba(255,255,255,170);")
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
            l.setStyleSheet("color: rgba(255,255,255,150); font-size: 11px;")
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
        vrow.addWidget(QLabel("Vol"))
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
        new = QPushButton("+ Playlist")
        new.clicked.connect(lambda: self.new_playlist())
        self.del_pl_btn = QPushButton("Delete")
        self.del_pl_btn.clicked.connect(self.delete_playlist)
        row.addWidget(self.pl_combo, 1)
        row.addWidget(new)
        row.addWidget(self.del_pl_btn)
        lay.addLayout(row)
        add = QPushButton("+ Add music files")
        add.clicked.connect(self.add_files)
        lay.addWidget(add)
        self.lib_list = QListWidget()
        self.lib_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.lib_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lib_list.customContextMenuRequested.connect(lambda pos: self.track_menu(self.lib_list, pos, "library"))
        self.lib_list.itemDoubleClicked.connect(lambda it: self.play_item(self.lib_list, it))
        lay.addWidget(self.lib_list, 1)
        hint = QLabel("Double-click to play. Right-click for more options.")
        hint.setStyleSheet("color: rgba(255,255,255,120); font-size: 11px;")
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
        self.search_box.setPlaceholderText("Search YouTube...")
        self.search_box.returnPressed.connect(self.do_search)
        go = QPushButton("Search")
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
        self.add_pl_btn = QPushButton("Add selected to playlist")
        self.add_pl_btn.clicked.connect(self.search_add_menu)
        save = QPushButton("Save to library")
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

        lay.addWidget(head("Components"))
        self.ver_lbl = QLabel("yt-dlp version: ...")
        lay.addWidget(self.ver_lbl)
        lay.addWidget(QLabel("libmpv: " + ("installed" if MPV_DLL.exists() else "missing")))
        lay.addWidget(QLabel("Deno: " + ("installed" if DENO.exists() else "missing")))
        brow = QHBoxLayout()
        b1 = QPushButton("Update yt-dlp")
        b1.clicked.connect(lambda: self.run_task(update_ytdlp, "Updating yt-dlp...", refresh=True))
        b2 = QPushButton("Re-download Deno")
        b2.clicked.connect(lambda: self.run_task(lambda: (install_deno(), "Deno updated.")[1], "Downloading Deno..."))
        brow.addWidget(b1)
        brow.addWidget(b2)
        brow.addStretch()
        lay.addLayout(brow)
        self.set_info = QLabel("")
        self.set_info.setWordWrap(True)
        self.set_info.setStyleSheet("color: rgba(255,255,255,170);")
        lay.addWidget(self.set_info)

        lay.addWidget(head("Preferences"))
        qrow = QHBoxLayout()
        qrow.addWidget(QLabel("YouTube audio quality"))
        self.q_combo = QComboBox()
        self.q_combo.addItems(["Best", "Data saver"])
        self.q_combo.setCurrentIndex(0 if self.cfg["quality"] == "best" else 1)
        self.q_combo.currentIndexChanged.connect(self.on_quality)
        qrow.addWidget(self.q_combo)
        qrow.addStretch()
        lay.addLayout(qrow)
        self.auto_chk = QCheckBox("Play the next song automatically")
        self.auto_chk.setChecked(self.cfg["autoplay"])
        self.auto_chk.toggled.connect(self.on_autoplay)
        lay.addWidget(self.auto_chk)
        self.blur_chk = QCheckBox("Blur window background (Windows 11, restart needed)")
        self.blur_chk.setChecked(self.cfg["blur"])
        self.blur_chk.toggled.connect(self.on_blur)
        lay.addWidget(self.blur_chk)

        lay.addWidget(head("Data"))
        lay.addWidget(QLabel(f"Saved in: {DATA_DIR}"))
        op = QPushButton("Open data folder")
        op.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR))))
        orow = QHBoxLayout()
        orow.addWidget(op)
        orow.addStretch()
        lay.addLayout(orow)
        lay.addStretch()
        return w

    # ---------------------------------------------------------- background
    def paintEvent(self, e):
        p = QPainter(self)
        a = 190 if self.acrylic else 255
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0.0, QColor(18, 20, 36, a))
        g.setColorAt(0.55, QColor(30, 22, 48, a))
        g.setColorAt(1.0, QColor(66, 18, 48, a))
        p.fillRect(self.rect(), g)
        glow = QRadialGradient(self.width() * 0.85, self.height() * 0.9, self.width() * 0.5)
        glow.setColorAt(0, QColor(190, 40, 90, 90))
        glow.setColorAt(1, QColor(190, 40, 90, 0))
        p.fillRect(self.rect(), glow)

    def showEvent(self, e):
        super().showEvent(e)
        if self.acrylic and not self._backdrop_done:
            self._backdrop_done = True
            apply_backdrop(int(self.winId()))

    # ---------------------------------------------------------- helpers
    def toast(self, text):
        self.status.setText(text)
        QTimer.singleShot(3500, lambda: self.status.setText(""))

    def run_task(self, fn, label, refresh=False):
        self.set_info.setText(label)
        t = Task(fn)
        t.ok.connect(lambda r: (self.set_info.setText(str(r)), refresh and self.refresh_versions()))
        t.failed.connect(lambda e: self.set_info.setText(f"Error: {e}"))
        self.tasks.append(t)
        t.start()

    def refresh_versions(self):
        t = Task(ytdlp_version)
        t.ok.connect(lambda v: self.ver_lbl.setText(f"yt-dlp version: {v}"))
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
        self.cfg["blur"] = on
        self.save_cfg()
        self.set_info.setText("Restart the app to apply this.")

    # ---------------------------------------------------------- library actions
    def current_playlist(self):
        i = self.pl_combo.currentIndex()
        return None if i <= 0 else self.pl_combo.currentText()

    def refresh_playlists(self, select=None):
        self.pl_combo.blockSignals(True)
        self.pl_combo.clear()
        self.pl_combo.addItem("All songs")
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
            self.lib_list.addItem("Empty. Add music files or save songs from Search.")
        for t in tracks:
            self.lib_list.addItem(make_item(t))

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add music", str(Path.home() / "Music"),
            "Audio files (*.mp3 *.flac *.m4a *.wav *.ogg *.opus *.aac);;All files (*.*)")
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
        self.toast(f"Added {len(files)} file(s).")

    def new_playlist(self, add=None):
        name, ok = QInputDialog.getText(self, "New playlist", "Playlist name:")
        name = name.strip()
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
        if QMessageBox.question(self, "Delete playlist",
                                f"Delete '{pl}'? Your songs stay in the library.") == QMessageBox.Yes:
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
        self.toast(f"Added {len(tracks)} song(s) to '{name}'.")

    def save_tracks(self, tracks):
        if not tracks:
            return
        for t in tracks:
            self.lib.add_track(t)
        self.lib.save()
        self.refresh_library_list()
        self.toast(f"Saved {len(tracks)} song(s) to your library.")

    def fill_playlist_menu(self, menu, tracks):
        for name in self.lib.playlists:
            add_action(menu, name, lambda n=name: self.add_to_playlist(n, tracks))
        if self.lib.playlists:
            menu.addSeparator()
        add_action(menu, "New playlist...", lambda: self.new_playlist(add=tracks))

    def selected_tracks(self, lw):
        return [i.data(Qt.UserRole) for i in lw.selectedItems() if i.data(Qt.UserRole)]

    def track_menu(self, lw, pos, where):
        items = [i for i in lw.selectedItems() if i.data(Qt.UserRole)]
        if not items:
            return
        tracks = [i.data(Qt.UserRole) for i in items]
        m = QMenu(self)
        add_action(m, "Play", lambda: self.play_item(lw, items[0]))
        self.fill_playlist_menu(m.addMenu("Add to playlist"), tracks)
        if where == "library":
            pl = self.current_playlist()
            if pl:
                add_action(m, "Remove from this playlist", lambda: self.remove_from_playlist(pl, tracks))
            add_action(m, "Remove from library", lambda: self.remove_from_library(tracks))
        else:
            add_action(m, "Save to library", lambda: self.save_tracks(tracks))
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
            self.toast("Select one or more results first.")
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
        self.res_list.addItem("Searching...")
        t = Task(lambda: search_youtube(q))
        t.ok.connect(self.show_results)
        t.failed.connect(lambda e: (self.res_list.clear(), self.res_list.addItem(f"Error: {e}")))
        self.tasks.append(t)
        t.start()

    def show_results(self, tracks):
        self.res_list.clear()
        if not tracks:
            self.res_list.addItem("No results.")
        for t in tracks:
            self.res_list.addItem(make_item(t))

    # ---------------------------------------------------------- playback
    def play_item(self, lw, item):
        if not item.data(Qt.UserRole):
            return
        self.queue = tracks_of(lw)
        self.qi = lw.row(item)
        self.play_current()

    def play_current(self):
        t = self.queue[self.qi]
        if t["kind"] == "file":
            src = t["path"]
            if not os.path.exists(src):
                self.artist_lbl.setText("File not found. It may have been moved.")
                return
        else:
            src = f"https://www.youtube.com/watch?v={t['vid']}"
        self.current = t
        self.got_dur = False
        self.title_lbl.setText(t["title"])
        self.artist_lbl.setText(t.get("artist") or "")
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

    def play_next(self):
        if self.qi + 1 < len(self.queue):
            self.qi += 1
            self.play_current()

    def play_prev(self):
        if not self.queue:
            return
        try:
            pos = self.player.time_pos or 0
        except Exception:
            pos = 0
        if pos > 3 or self.qi == 0:
            self.player.seek(0, reference="absolute")
        else:
            self.qi -= 1
            self.play_current()

    def toggle_pause(self):
        if not self.current:
            return
        try:
            if self.player.idle_active:
                self.play_current()
                return
        except Exception:
            pass
        self.player.pause = not self.player.pause

    def do_seek(self):
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

    def on_paused(self, p):
        self.play_btn.setText("\u25B6" if p else "\u275A\u275A")

    def on_idle(self, idle):
        if not idle:
            self.started = True
            return
        if not self.started:
            return
        self.started = False
        if not self.got_dur:
            self.artist_lbl.setText("Couldn't play this. Try Settings \u2192 Update yt-dlp.")
            return
        if self.cfg["autoplay"] and self.qi + 1 < len(self.queue):
            self.play_next()
        else:
            self.seek.setValue(0)
            self.t_left.setText("0:00")
            self.play_btn.setText("\u25B6")

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
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(28, 25, 48))
    pal.setColor(QPalette.WindowText, Qt.white)
    pal.setColor(QPalette.Base, QColor(36, 32, 60))
    pal.setColor(QPalette.AlternateBase, QColor(44, 40, 70))
    pal.setColor(QPalette.Text, Qt.white)
    pal.setColor(QPalette.Button, QColor(48, 43, 78))
    pal.setColor(QPalette.ButtonText, Qt.white)
    pal.setColor(QPalette.Highlight, QColor(150, 90, 200))
    pal.setColor(QPalette.HighlightedText, Qt.white)
    pal.setColor(QPalette.PlaceholderText, QColor(255, 255, 255, 120))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)

    if not all(path.exists() for _, path, _ in DEPENDENCIES):
        if SetupDialog().exec() != QDialog.Accepted:
            os._exit(0)
    try:
        win = MainWindow()
    except Exception as e:
        QMessageBox.critical(None, "Startup error", f"Could not start the audio engine:\n{e}")
        return
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
