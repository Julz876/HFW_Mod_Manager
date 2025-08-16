import sys, time, io
import os, re, json
from PIL import Image
import shutil, subprocess, tempfile
import urllib.request
from zipfile import ZipFile, BadZipFile
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import QWidget
import qdarktheme
import markdown
from utils.stream import (_run_and_copy_core_stream)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage
import logging
import zlib
import hashlib

# Feature flags
FEAT_REGISTRY_META  = True
FEAT_ORDER_UI       = True
FEAT_ACTIVATED_SAVE = True
FEAT_IMPORT_ALL     = True
FEAT_SMART_DELETE   = True
FEAT_STRICT_HASH    = False
FEAT_UPDATE_CHECKER = False
FEAT_HELP_WEBENGINE = False
FEAT_CONFLICT_COLOR = True
FEAT_PILLOW_ICC     = False
# -------------------------

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

REGISTRY_PATH = Path.cwd() / "meta.ini"
# MAX_TOTAL_UNCOMPRESSED_SIZE = 250 * 1024 * 1024  # 250 MB
MAX_TOTAL_UNCOMPRESSED_SIZE = 800 * 1024 * 1024  # Testing
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
KNOWN_HASHES = {
    "streaming_graph.core": {
        "crc32": 0x6bc24389,
        "sha1": "a6860845590ef18c475966f5f4762b49ad0fe258"
    },
    "streaming_links.stream": {
        "crc32": 0x228280f6,
        "sha1": "4f06f9b73d8852979bd777c8a707668657ec5dd5"
    }
}

img_ext = ['.png', '.jpg', '.jpeg','.bmp', '.gif' ]
temp_ = Path.cwd() / 'temp_'
temp_drag = Path.cwd() / 'temp_drag'


def _load_mod_registry(path: Path = REGISTRY_PATH) -> dict:
    try:
        if path.exists():
            data = json.load(open(path, "r", encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[!] meta.ini read error: {e}")
    return {}

def _normalize_key(name: str) -> str:
    return name.replace(" ", " ")

def _normalize_mod_name(raw_name: str) -> str:
    """
    Extracts the base mod name by removing versioning patterns like:
    - <name>-<pkg>-<major>-<minor>-<build>
    - <name>-<pkg>-<major>-<minor>
    - <name>-<pkg>-<major>
    """
    # Match patterns with major and minor versions
    match_full = re.match(r"(.+?)-\d+-(\d+)-(\d+)-\d+$", raw_name)
    match_partial = re.match(r"(.+?)-\d+-(\d+)-\d+$", raw_name)

    if match_full:
        base, _, _ = match_full.groups()
        return base.strip() 
    elif match_partial:
        base, _ = match_partial.groups()
        return base.strip()
    else:
        return raw_name.strip()

def _normpath(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))

def _candidate_roots(mods_dir: Path, display_name: str, top_item: QTreeWidgetItem) -> list[Path]:
    if not FEAT_SMART_DELETE:
        return []

    wanted = display_name.strip()
    cand = []

    # From top item (UserRole)
    ur = top_item.data(0, Qt.UserRole)
    if isinstance(ur, str) and ur:
        p = Path(ur)
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        if mods_dir in rp.parents or rp == mods_dir:
            if rp.is_dir():
                # Heuristic
                parent = rp.parent
                if parent != mods_dir:
                    cand.append(parent)
            # Directly in the mod root
            if rp != mods_dir and rp.is_dir():
                cand.append(rp)

    # From meta.ini by normalized key match
    reg = _load_mod_registry()
    for key, meta in reg.items():
        if key == "_meta":
            continue
        if _normalize_mod_name(_normalize_key(key)) == wanted:
            src = meta.get("source_path", "")
            if src:
                ps = Path(src)
                try:
                    rps = ps.resolve()
                except Exception:
                    rps = ps
                if mods_dir in rps.parents or rps == mods_dir:
                    if rps.is_dir() and rps.parent != mods_dir:
                        cand.append(rps.parent)
                    if rps.is_dir():
                        cand.append(rps)

    # Scan mods/ for directories
    for d in mods_dir.iterdir():
        try:
            name_stem = _normalize_key(d.stem)
            if _normalize_mod_name(name_stem) == wanted:
                cand.append(d)
        except Exception:
            pass

    # Dedup by normalized absolute path
    uniq = []
    seen = set()
    for p in cand:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        if not rp.exists():
            continue
        if not (rp == mods_dir or mods_dir in rp.parents):
            continue
        key = _normpath(str(rp))
        if key not in seen:
            seen.add(key)
            uniq.append(rp)
    return uniq


def _compute_crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xffffffff

def _compute_sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()

def _validate_original_file(file_path: Path) -> tuple[bool, str]:
    name = file_path.name
    if name not in KNOWN_HASHES:
        return True, ""  # skip unknown files

    expected_crc = KNOWN_HASHES[name]["crc32"]
    expected_sha1 = KNOWN_HASHES[name]["sha1"]

    try:
        data = file_path.read_bytes()
        actual_crc = _compute_crc32(data)
        actual_sha1 = _compute_sha1(data)

        crc_match = actual_crc == expected_crc
        sha1_match = actual_sha1.lower() == expected_sha1.lower()

        if not (crc_match and sha1_match):
            detail = (
                f"Expected CRC32: {expected_crc:#010x}\n"
                f"Actual CRC32:   {actual_crc:#010x}\n\n"
                f"Expected SHA1:  {expected_sha1}\n"
                f"Actual SHA1:    {actual_sha1}"
            )
            # sys.exit(main())

            return False, detail
        return True, ""
    except Exception as e:
        return False, f"[!] Error reading {name}: {e}"


def _write_json_atomic(path: Path, data: dict):
    # tmp = Path(tempfile.mkstemp(prefix="modmeta_", suffix=".json")[1])
    tmp = Path.cwd() / "modmeta_.json"
    try:
        if FEAT_REGISTRY_META:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        os.replace(tmp, path)  # atomic on the same filesystem
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _find_mod_images(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None

    # Prioritize known keywords
    keywords = ['preview', 'variation', 'screenshot', 'image']
    for f in folder.iterdir():
        if f.suffix.lower() in img_ext and any(k in f.stem.lower() for k in keywords):
            return f

    # Fallback to any image
    for f in folder.iterdir():
        if f.suffix.lower() in img_ext:
            return f

    return None

def _load_pix(path: Path) -> QPixmap:
    if FEAT_PILLOW_ICC:
        from PIL import Image
        with Image.open(path) as img:
            if "icc_profile" in img.info:
                del img.info["icc_profile"]
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                img.save(tmp.name, format="PNG")
                return QPixmap(tmp.name)
    return QPixmap(str(path))


def _safe_json_load(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _file_sha1(path: Path) -> str:
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""

def _file_mtime(path: Path) -> int:
    try:
        return int(path.stat().st_mtime)
    except Exception:
        return 0

def _diff_fields(old: dict, new: dict, keys: list[str]) -> dict:
    diffs = {}
    for k in keys:
        ov = old.get(k)
        nv = new.get(k)
        if ov != nv:
            diffs[k] = (ov, nv)
    return diffs

def _short(val, n=80):
    if val is None:
        return ""
    s = str(val).replace("\r\n", "\n").replace("\r", "\n").strip()
    s = " ".join(s.split())  # collapse all whitespace/newlines
    return (s[:n] + "…") if len(s) > n else s


class PackingWorker(QObject):
    finished = pyqtSignal(bool, str)  # success, message
    def __init__(self, manager: 'ModManager'):
        super().__init__()
        self.manager = manager

    def run(self):
        try:
            success, message = self.manager.pack_mods_worker()
            self.finished.emit(success, message)
        except Exception as e:
            self.finished.emit(False, f"Packing failed:\n{e}")


class DropTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setSelectionMode(self.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragDropOverwriteMode(False)
        self.setDragDropMode(self.DropOnly)
        self.itemChanged.connect(self._on_item_changed)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        self.drag_counter = 0

        # Create overlay on parent, not self
        self.drop_overlay = QLabel("Drop your mods here", self.window())
        self.drop_overlay.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 128);
                color: white;
                font: bold 20px;
                border: 2px dashed white;
            }
        """)
        self.drop_overlay.setAlignment(Qt.AlignCenter)
        self.drop_overlay.hide()
        self.drop_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)  # allow drag-through

        # Initially set size and position
        self.update_overlay_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_overlay_geometry()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.update_overlay_geometry()

    def update_overlay_geometry(self):
        if not self.drop_overlay:
            return
        top_left = self.mapTo(self.window(), QPoint(0, 0))
        self.drop_overlay.setGeometry(QRect(top_left, self.size()))
        self.drop_overlay.raise_()  # bring to top


    def _on_item_changed(self, item, col):
        parent = item.parent()
        # only handle child items
        if parent is None:
            return
        # only process user-checkable children (skip shared_files)
        if not (item.flags() & Qt.ItemIsUserCheckable):
            return
        # if a child was checked, uncheck its siblings
        if item.checkState(0) == Qt.Checked:
            for i in range(parent.childCount()):
                sibling = parent.child(i)
                if sibling is not item and (sibling.flags() & Qt.ItemIsUserCheckable):
                    sibling.setCheckState(0, Qt.Unchecked)
        parent.setCheckState(0, Qt.PartiallyChecked)
    
    def show_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item:
            return

        mod_path = item.data(0, Qt.UserRole)
        if not mod_path:
            return

        path = Path(mod_path)
        img = _find_mod_images(path)# or (path / 'preview.png')
        if not img:
            return

        menu = QMenu()
        act = menu.addAction("🖼️ Show Preview")
        action = menu.exec_(self.viewport().mapToGlobal(pos))
        if action == act:
            self.show_preview_image(img)

    def show_preview_image(self, img_path: Path):
        dlg = QDialog(self)
        dlg.setWindowTitle("Mod Preview")
        dlg.setMinimumSize(600, 600)
        
        layout = QVBoxLayout(dlg)
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        label.setPixmap(_load_pix(img_path).scaled(580, 580, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        layout.addWidget(label)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        dlg.exec_()


    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self.drag_counter += 1
            QTimer.singleShot(0, self.update_overlay_geometry)
            self.drop_overlay.show()
            event.accept()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self.drag_counter -= 1
        if self.drag_counter <= 0:
            self.drop_overlay.hide()
            self.drag_counter = 0
        super().dragLeaveEvent(event) 

    def dropEvent(self, event):
        self.drag_counter = 0
        self.drop_overlay.hide()
        if not event.mimeData().hasUrls():
            return super().dropEvent(event)

        main_win = self.window()
        mods_folder = None
        if hasattr(main_win, 'select_game_dir'):
            gd = main_win.select_game_dir.text().strip()
            if gd:
                mods_folder = Path(gd) / 'mods'
                mods_folder.mkdir(parents=True, exist_ok=True)

        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            mod_name = _normalize_mod_name(path.with_suffix('').name)

            result = None
            if path.is_dir():
                result = self.handle_dir_input(path, mod_name, main_win)
            elif path.suffix.lower() == '.zip':
                result = self.handle_zip_input(path, mods_folder, main_win)
            else:
                QMessageBox.warning(self, "Error", "Not a compatible file format.")
                continue

            if not result:
                continue

            # mod_path, display_name = result
            # self.add_mod_to_tree(mod_name, display_name, mod_path, path, mods_folder)

            # Normalize to a list of (mod_path, display_name)
            items = result if isinstance(result, list) else [result]
            for mod_path, display_name in items:
                self.add_mod_to_tree(mod_name, display_name, mod_path, path, mods_folder)

        event.acceptProposedAction()

        # Refresh mod list in main window
        main_win = self.window()
        if hasattr(main_win, 'refresh_list'):
            main_win.refresh_list()


    def handle_zip_input(self, path: Path, mods_folder: Path, main_win) -> tuple[Path, str] | None:
        mod_name = _normalize_mod_name(path.with_suffix('').name)
        display_name = mod_name
        self.tmp_root = Path.cwd() / "temp_drag"

        try:
            with ZipFile(path) as z:
                # Safety Checks
                total_uncompressed = 0
                for info in z.infolist():
                    # ZIP bomb: file too large
                    if info.file_size > MAX_FILE_SIZE:
                        QMessageBox.warning(self, "Unsafe ZIP", f"File too large in zip: {info.filename} ({info.file_size} bytes)")
                        return None

                    # Path traversal: e.g., ../../Windows/system32
                    norm_path = Path(info.filename)
                    if ".." in norm_path.parts:
                        QMessageBox.warning(self, "Unsafe ZIP", f"Suspicious path in zip: {info.filename}")
                        return None

                    total_uncompressed += info.file_size

                # ZIP bomb: total size too large
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_SIZE:
                    QMessageBox.warning(self, "Unsafe ZIP", f"Uncompressed ZIP too large: {total_uncompressed / (1024*1024):.1f} MB")
                    return None

                dirs_with_var = set(info.filename.split('/')[0] for info in z.infolist() if info.filename.endswith('variation.png'))

            if len(dirs_with_var) > 1:
                with ZipFile(path) as z:
                    for info in z.infolist():
                        top_folder = info.filename.split('/')[0]
                        if top_folder in dirs_with_var:
                            z.extract(info, self.tmp_root)
                dlg = VariationDialog(mod_name, self.tmp_root, parent=main_win)
                if dlg.exec_() == QDialog.Accepted:
                    # return many or one
                    return dlg.selected_variations(mod_name)
                else:
                    return None
            else:
                # no variants
                if mods_folder:
                    target = mods_folder / mod_name
                    if target.exists():
                        shutil.rmtree(target)
                    with ZipFile(path) as z:
                        z.extractall(target)
                    return target, mod_name
        except Exception as e:
            print(f"ZIP Error: {e}")
            return None

    def handle_dir_input(self, path: Path, mod_name: str, main_win) -> tuple[Path, str] | None:
        vars_dir = [d for d in path.iterdir() if d.is_dir() and _find_mod_images(d) is not None] # keep sub-folders with mod images
        if len(vars_dir) > 1:
            dlg = VariationDialog(mod_name, path, parent=main_win)
            if dlg.exec_() == QDialog.Accepted:
                # return many or one
                return dlg.selected_variations(mod_name)
            else:
                return None
        return path, mod_name

    def add_mod_to_tree(self, mod_name: str, display_name: str, mod_path: Path, source_path: Path, mods_folder: Path):
        # Find or create top-level item
        top = None
        for i in range(self.topLevelItemCount()):
            if self.topLevelItem(i).text(0) == mod_name:
                top = self.topLevelItem(i)
                break
        if not top:
            top = QTreeWidgetItem(self, [mod_name])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsTristate | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            top.setCheckState(0, Qt.Unchecked)
            top.setData(0, Qt.UserRole, str(mod_path)) # Add top path to preview images


        # Skip duplicate children
        if any(top.child(j).text(0) == display_name for j in range(top.childCount())):
            return

        # Add mod variation as child
        child = QTreeWidgetItem(top, [display_name])
        child.setFlags(child.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        child.setCheckState(0, Qt.Unchecked)
        child.setData(0, Qt.UserRole, str(mod_path))
        top.addChild(child)

        # Copy mod into mods/ folder
        if mods_folder and mod_path:
            base_target = mods_folder / mod_name
            target = base_target / Path(mod_path).name

            try:
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(mod_path, target, dirs_exist_ok=True)

                self.remove_duplicated_subfolder(base_target)

                shared_src = source_path / 'shared_files'
                if shared_src.is_dir():
                    shutil.copytree(shared_src, base_target / 'shared_files', dirs_exist_ok=True)

            except Exception as e:
                print(f"Error copying mod to mods/: {e}")

        # Add shared_files to tree if present
        if source_path.is_dir():
            shared = source_path / 'shared_files'
            if shared.is_dir():
                if not any(top.child(j).text(0) == 'shared_files' for j in range(top.childCount())):
                    sf = QTreeWidgetItem(top, ['shared_files'])
                    sf.setCheckState(0, Qt.Checked)
                    sf.setData(0, Qt.UserRole, str(shared))
                    sf.setFlags(sf.flags() & ~Qt.ItemIsUserCheckable)

            root_src = source_path
            if not (source_path / 'shared_files').exists():
                for f in root_src.iterdir():
                    if f.suffix.lower() in ('.json', '.stream', '.core', '.png', '.jpg', '.jpeg'):
                        shutil.copy(f, base_target / f.name)

        elif source_path.suffix.lower() == '.zip':
            if mods_folder:
                with ZipFile(source_path) as z:
                    for info in z.infolist():
                        if info.filename.startswith('shared_files/') and not info.is_dir():
                            rel = Path(info.filename)
                            dest = (mods_folder / mod_name / rel)
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with z.open(info) as src, open(dest, 'wb') as dst:
                                dst.write(src.read())

                if not (mods_folder / mod_name / 'shared_files').exists():
                    with ZipFile(source_path) as z:
                        for info in z.infolist():
                            parts = Path(info.filename)
                            if len(parts.parts) == 1 and parts.suffix.lower() in ('.json', '.stream', '.core', '.png', '.jpg', '.jpeg'):
                                dest = (mods_folder / mod_name) / parts.name
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                with z.open(info) as src, open(dest, 'wb') as dst:
                                    dst.write(src.read())

    def remove_duplicated_subfolder(self, folder_path):
        # Normalize path and get the base folder name
        folder_path = os.path.normpath(folder_path)
        base_name = os.path.basename(folder_path)

        # Construct the path to the potential subfolder
        target_subfolder = os.path.join(folder_path, base_name)

        if os.path.isdir(target_subfolder):
            try:
                shutil.rmtree(target_subfolder)
                print(f"Delete duplicated subfolder: {target_subfolder}")
            except Exception as e:
                print(f"Error deleting '{target_subfolder}': {e}")


class VariationDialog(QDialog):
    def __init__(self, mod_name, mod_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Variation")
        self.mod_path = Path(mod_path)

        # Gather valid variation folders with preview images
        self.variations = [
            d for d in self.mod_path.iterdir()
            if d.is_dir() and _find_mod_images(d) is not None
        ]

        # Main layout
        # ----------------------------------------------------------------------
        dlg_layout = QVBoxLayout(self)

        # Info label
        top_label = QLabel(
            f'The mod "{mod_name}" contains multiple variations. '
            "Please select the one you'd like to install."
        )
        dlg_layout.addWidget(top_label)

        # Split List + Preview
        # ----------------------------------------------------------------------
        split = QHBoxLayout()

        # variation list
        self.list_widget = QListWidget()
        self.list_widget.setFixedWidth(300)
        for var in self.variations:
            self.list_widget.addItem(var.name)
        self.list_widget.currentRowChanged.connect(self.update_preview)
        split.addWidget(self.list_widget, 1)

        # Image Preview
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(300, 300)  
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        split.addWidget(self.image_label, 3)

        split.addStretch()
        dlg_layout.addLayout(split)

        # Buttons
        # ----------------------------------------------------------------------
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")

        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

        if FEAT_IMPORT_ALL:
            self.import_all = QCheckBox("Import All")
            self.import_all.setToolTip("Import all available variants.")
            btn_layout.addWidget(self.import_all)

        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        dlg_layout.addLayout(btn_layout)

        # Select first item
        # ----------------------------------------------------------------------
        if self.variations:
            self.list_widget.setCurrentRow(0)
        else:
            QMessageBox.warning(self, "No Valid Variations", "No variations with preview images found.")
            self.reject()

    def selected_variations(self, mod_name: str) -> list[tuple[Path, str]]:
        """Return (path, display_name) pairs for either the chosen item or all."""
        if FEAT_IMPORT_ALL and getattr(self, "import_all", None) and self.import_all.isChecked():
            return [(v, f"{mod_name}/{v.name}") for v in self.variations]
        # single
        idx = self.list_widget.currentRow()
        if 0 <= idx < len(self.variations):
            v = self.variations[idx]
            return [(v, f"{mod_name}/{v.name}")]
        return []

    def update_preview(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.variations):
            self.image_label.clear()
            return
        
        var = self.variations[idx]
        img_path = _find_mod_images(var)

        if img_path and img_path.exists():
            # pix = QPixmap(str(img_path))
            pix = _load_pix(img_path)
            if not pix.isNull():
                self.image_label.setPixmap(pix.scaled(
                    self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                ))
        else:
            self.image_label.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_preview(self.list_widget.currentRow())


class ModManager(QWidget):
    CONFIG_PATH = Path.cwd() / 'decima.ini'

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HFW MOD MANAGER by KingJulz")

        self.icon_path = os.path.join(Path(__file__).parent,'res','hfw_mm_icon_03.png')
        self.setWindowIcon(QIcon(self.icon_path))

        self.metadata = {}
        self.version = 0.6 # Current app version
        self.prefs = QSettings()

        # Clean up old lists
        legacy = Path.cwd() / 'activated.list'
        if legacy.exists():
            legacy.rename(legacy.with_suffix('.legacy'))
        legacy_ = Path.cwd() / 'mod_order.json'
        if legacy_.exists():
            legacy_.rename(legacy_.with_suffix('.legacy'))
        legacy_ = Path.cwd() / 'mod.meta'
        if legacy_.exists():
            legacy_.rename(legacy_.with_suffix('.meta_legacy'))

        # Temp workspace for packing
        self.temp_dir = Path.cwd() / 'pack'
        self.backup_dir = Path.cwd() / 'backup'
        self.init_ui()
        self.load_config()

        self.pack_tool = Path.cwd() / 'Decima_pack.exe'
        if not self.pack_tool.exists():
            if hasattr(self, 'btn_pack'):
                self.btn_pack.setEnabled(False)
                QMessageBox.critical(
                    self,
                    "Missing Tool",
                    "⚠️ Required tool not found:\n\nDecima_pack.exe\n\nPlease place it in the same folder as HFW_MM.exe."
                )

            self.status_label.setText("⚠️ Packing disabled: Decima_pack.exe missing.")

        self.temp_ = temp_
        self.temp_drag = temp_drag

        # if temp_ or temp_drag:
        #     self.clear_temp(self.temp_, self.temp_drag)

        self.refresh_list()

        print("Settings file:", self.prefs.fileName()) # For testing

    def init_ui(self):
        self.temp_dir.mkdir(exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        tabs = QTabWidget(self)

        # Mods tab
        mods_page = QWidget()
        mods_layout = QVBoxLayout(mods_page)
        main_layout = mods_layout

        # Game selector
        gl = QHBoxLayout()
        self.select_game_dir = QLineEdit(); self.select_game_dir.setPlaceholderText("Select game path...")
        self.select_game_dir.setFixedWidth(450)
        btn_game = QPushButton("Browse"); btn_game.clicked.connect(self.browse_game)
        gl.addWidget(QLabel("Game Folder:")); gl.addWidget(self.select_game_dir); gl.addWidget(btn_game)
        btn_game.setToolTip("Locate the game directory containing the HorizonForbiddenWest.exe.")

        # "Open mods folder" button
        self.btn_open_mods = QPushButton("📂 Open Mods")
        self.btn_open_mods.setFixedWidth(150)
        self.btn_open_mods.clicked.connect(self.open_mods_folder)
        gl.addWidget(self.btn_open_mods)
        self.btn_open_mods.setToolTip("Opens the 'mods' folder inside your game directory.")

        # Saved path is loaded and valid
        self.update_open_mods_visibility()
        
        main_layout.addLayout(gl)
        gl.addStretch()

        tl = QHBoxLayout()

        # Dark Mode Toggle
        self.dark_mode_checkbox = QCheckBox("Dark Mode")
        self.dark_mode_checkbox.stateChanged.connect(self.on_dark_mode_toggled)
        tl.addWidget(self.dark_mode_checkbox)

        # Conlict Mode Toggle
        self.conflict_check = QCheckBox("Flags")
        self.conflict_check.setChecked(False)
        tl.addWidget(self.conflict_check)
        self.conflict_check.setToolTip("Detect and highlight conflicted files.")

        # Reorder/sort by; buttons
        btn_up = QPushButton("↑ Move Up")
        btn_down = QPushButton("↓ Move Down")
        btn_up.clicked.connect(self.move_selected_mod_up)
        btn_down.clicked.connect(self.move_selected_mod_down)
        tl.addWidget(btn_up)
        tl.addWidget(btn_down)
        btn_up.setToolTip("Moves the selected item up in the list.")
        btn_down.setToolTip("Moves the selected item down in the list.")

        if FEAT_ORDER_UI:
            btn_sort_priority = QPushButton("↕ Sort by Group")
            btn_sort_priority.clicked.connect(self.sort_mods_by_priority)
            tl.addWidget(btn_sort_priority)
            btn_sort_priority.setToolTip("Sort the list by priority group.")

            btn_save_order = QPushButton("💾 Save Order")
            btn_save_order.clicked.connect(self.save_mod_order)
            tl.addWidget(btn_save_order)
            btn_save_order.setToolTip("Save mod list current order.")

            btn_load_order = QPushButton("↺ Load Order")
            btn_load_order.clicked.connect(self.apply_saved_mod_order)
            tl.addWidget(btn_load_order)
            btn_load_order.setToolTip("Load previously saved order.")

            self.notify_meta_changes = QCheckBox("Notify change")
            self.notify_meta_changes.setToolTip("Notify when a mod's modinfo.json changed since last scan.")
            # load saved value
            notify_on = self.prefs.value("ui/notify_meta_changes", False, type=bool)
            self.notify_meta_changes.setChecked(notify_on)
            self.notify_meta_changes.stateChanged.connect(
                lambda _: (self.prefs.setValue("ui/notify_meta_changes", self.notify_meta_changes.isChecked()),
                        self.prefs.sync())
            )
            tl.addWidget(self.notify_meta_changes)

        tl.addStretch()
        main_layout.addLayout(tl)

        # Split drop list and buttons
        sl = QHBoxLayout()
        left = QFrame(); ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Drag & Drop mods:")); self.mod_list = DropTreeWidget(self); ll.addWidget(self.mod_list)
        sl.addWidget(left, 3)
        right = QFrame(); rl = QVBoxLayout(right)
        
        about_layout = QVBoxLayout()
        logo_path = self.icon_path
        self.logo_thumb= QLabel()
        pixmap = _load_pix(logo_path)
        self.logo_thumb.setFixedSize(200, 200)
        self.logo_thumb.setPixmap(pixmap)
        self.logo_thumb.setScaledContents(True)
        
        self.version_label = QLabel(f"Version: {self.version}")
        github_link = QLabel("Check for updates at: <a href='https://github.com/Julz876/HFW_Mod_Manager/releases'>HFW Mod Manager</a>")
        github_link.setOpenExternalLinks(True)

        rl.addWidget(self.logo_thumb)
        rl.addWidget(self.version_label)
        rl.addWidget(github_link)

        # Schedule update checks
        self.current_version = self.version_label.text().split(":")[-1].strip()
        # Initial check 1s
        if FEAT_UPDATE_CHECKER:
            QTimer.singleShot(1000, self.check_for_updates)

            self.update_timer = QTimer(self)
            self.update_timer.timeout.connect(self.check_for_updates)
            self.update_timer.start(1000 * 60 * 10)  # every 10 min

        for txt, func, icon_name, tip in [
            ("Check All", self.check_all, 'SP_DialogYesButton', "Mark all items in the list."),
            ("Un-Check All", self.uncheck_all, 'SP_DialogNoButton', "Un-mark all items in the list."),
            ("Refresh Mod List", self.refresh_list, 'SP_DialogResetButton', "Click to reload list and organize entries from A to Z"),
            ("Remove Selected Mods", self.remove_selected, 'SP_DialogCancelButton', "Delete the currently selected mods from your list and mods folder"),
            ("Restore Game Files", self.restore_default, 'SP_DialogOkButton', "Revert game files to original, unmodified state."),
            ("Pack Activated Mods", self.pack_mods, 'SP_DialogSaveButton', "Bundle all active mods into a single package.")
        ]:
            btn = QToolButton()
            btn.setFixedSize(200, 60)
            btn.clicked.connect(func)
            sp_constant = getattr(QStyle, icon_name)
            btn.setIcon(self.style().standardIcon(sp_constant))
            btn.setIconSize(QSize(50, 50))
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setText(txt)

            if txt == "Pack Activated Mods":
                self.btn_pack = btn  # Save reference for missing pack tool

            if tip:
                btn.setToolTip(tip)

            rl.addWidget(btn)

        rl.addStretch()
        sl.addWidget(right, 1)
        sl.addLayout(about_layout)

        right2 = QFrame(); pl = QVBoxLayout(right2)

        # image viewer
        self.image_label2 = QLabel(self)
        self.image_label2.setFixedSize(400, 400)
        self.image_label2.setAlignment(Qt.AlignCenter)
        pl.addWidget(self.image_label2)
        self.mod_list.currentItemChanged.connect(self.on_mod_selected)
        default_icon_path = self.icon_path
        pix = _load_pix(default_icon_path)
        self.image_label2.setPixmap(
            pix.scaled(self.image_label2.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,)
        )

        self.meta_view = QVBoxLayout()
        self.meta_lbl = QLabel("Mod-Name")
        self.meta_lbl.setFont(QFont("Roboto", 12, QFont.Bold))   
        self.meta_author = QLabel("")
        self.meta_version = QLabel("")
        self.meta_notes = QLabel("")
        self.meta_link = QLabel("Please visit the <a href='https://www.nexusmods.com/games/horizonforbiddenwest'>Nexusmods</a> site")
        self.meta_link.setOpenExternalLinks(True)
        self.meta_view.addWidget(self.meta_lbl)
        self.meta_view.addWidget(self.meta_author)
        self.meta_view.addWidget(self.meta_version)
        self.meta_view.addWidget(self.meta_notes)
        self.meta_view.addStretch()
        self.meta_view.addWidget(self.meta_link)
        pl.addLayout(self.meta_view)

        sl.addWidget(right2, 2)
        main_layout.addLayout(sl)

        # Status Bar
        status = QHBoxLayout()
        self.status_label = QLabel("Status: Idle"); status.addWidget(self.status_label)
        status.addStretch()
        special_thanks = "Special Thanks: - id-daemon - HardcoreHobbyist - hornycopter"
        thanks = QLabel(special_thanks); status.addWidget(thanks)

        main_layout.addLayout(status)
        tabs.addTab(mods_page, "Management")

        # Enable for V4
        # Tools Tab
        # tabs.addTab(StreamPacking(self), "Tools")

        # Help Tab
        if FEAT_HELP_WEBENGINE:
            tabs.addTab(Help(self), "Help")
        else:
            from PyQt5.QtGui import QDesktopServices
            help_stub = QWidget()
            stub_layout = QVBoxLayout(help_stub)
            help_btn = QPushButton("Open Online Help")
            help_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://hfw-mm.gitbook.io/hfw-mm-docs/")))
            stub_layout.addWidget(help_btn, alignment=Qt.AlignCenter)
            tabs.addTab(help_stub, "Help")
        
        # Tab widget fill window
        outer = QVBoxLayout(self)
        outer.addWidget(tabs)
    
    def load_config(self):
        # Read bare cache path from decima.ini (should include LocalCacheWinGame)
        if self.CONFIG_PATH.exists():
            cache_path = Path(self.CONFIG_PATH.read_text().strip())
            if cache_path.name == 'LocalCacheWinGame':
                game_path = cache_path.parent
                self.select_game_dir.setText(str(game_path))
                self.post_browse_setup(game_path)
            elif cache_path.exists():
                self.select_game_dir.setText(str(cache_path))
                self.post_browse_setup(cache_path)

        dark_on = self.prefs.value("ui/dark_mode", True, type=bool)
        self.dark_mode_checkbox.blockSignals(True)
        self.dark_mode_checkbox.setChecked(dark_on)
        self.dark_mode_checkbox.blockSignals(False)
        self.apply_dark_mode(dark_on)

    def save_config(self, game_path: Path):
        # Save cache path including LocalCacheWinGame
        cache_dir = game_path / 'LocalCacheWinGame'
        with open(self.CONFIG_PATH, 'w') as f:
            f.write(str(cache_dir))
        
    def on_dark_mode_toggled(self, checked: bool):
        self.prefs.setValue("ui/dark_mode", checked)
        self.apply_dark_mode(checked)

    def apply_dark_mode(self, enabled: bool):
        app = QApplication.instance()
        if enabled:
            app.setStyleSheet(qdarktheme.load_stylesheet())
        else:
            # app.setStyleSheet("")
            app.setStyleSheet("QWidget { color: black; }")


    def check_for_updates(self):
        if not FEAT_UPDATE_CHECKER:
            return
        """Conditional‐GET against GitHub; on 403 use cached version, on 304 do nothing."""
        settings = self.prefs
        url = "https://api.github.com/repos/Julz876/HFW_Mod_Manager/releases/latest"
        headers = {"User-Agent": "HFW Mod Manager"}
        # Send the last ETag so GitHub can reply “304 Not Modified”
        if et := settings.value("update/etag", ""):
            headers["If-None-Match"] = et

        req = urllib.request.Request(url, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return
            if e.code == 403:
                cached = settings.value("update/latest_version", self.current_version)
                print(f"[Update] rate-limited; using cached latest = {cached}")
                return
            print(f"[Update] HTTP error {e.code}: {e}")
            return
        except Exception as e:
            print(f"[Update] failed: {e}")
            return

        data = json.loads(resp.read().decode())
        if new_et := resp.getheader("ETag"):
            settings.setValue("update/etag", new_et)
            settings.sync()
        latest = data.get("tag_name", "").lstrip("v")
        settings.setValue("update/latest_version", latest)
        settings.sync()

        # Compare versions
        def ver_tuple(v): 
            return tuple(int(x) for x in v.split(".") if x.isdigit())

        if ver_tuple(latest) > ver_tuple(self.current_version):
            msg = (
                f"A new version is available:\n\n"
                f"  Installed: {self.current_version}\n"
                f"  Latest:    {latest}\n\n"
                "Click OK to open the releases page."
            )
            if QMessageBox.information(self, "Update Available", msg,
                                       QMessageBox.Ok | QMessageBox.Cancel) == QMessageBox.Ok:
                QDesktopServices.openUrl(QUrl("https://github.com/Julz876/HFW_Mod_Manager/releases"))
    

    def browse_game(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Game Folder")
        if not folder:
            return

        exe_name = "HorizonForbiddenWest.exe"
        exe_path = Path(folder) / exe_name

        if exe_path.is_file():
            game_path = Path(folder)
            self.select_game_dir.setText(str(game_path))
            self.save_config(game_path)
            self.post_browse_setup(game_path)
            if self.btn_open_mods:
                self.update_open_mods_visibility()
        else:
            QMessageBox.warning(
                self, "Error",
                f"Invalid game folder selected.\n\n'{exe_name}' was not found in:\n{folder}"
            )
            self.select_game_dir.setText("")
            self.btn_open_mods.hide()

    def post_browse_setup(self, game_path: Path):
        # create and sync 'mods' folder
        mods_folder = game_path / 'mods'
        mods_folder.mkdir(parents=True, exist_ok=True)
        self.refresh_list()
        # backup and .org copies
        pkg = game_path / 'LocalCacheWinGame' / 'package'
        pkg.mkdir(parents=True, exist_ok=True)

        for f in ['streaming_graph.core', 'streaming_links.stream']:
            orig = pkg / f
            if not orig.exists():
                continue

            if FEAT_STRICT_HASH:
                # Validate before backing up
                valid, detail = _validate_original_file(orig)
                if not valid:
                    QMessageBox.critical(
                        self,
                        f"Hash Mismatch - {f}",
                        f"The original file does not match known-good hashes."
                        f"\n\n{detail}\n\n"
                        f"Backup skipped for safety.\n\nDelete this file:\n{orig}\n\nThen verify your game files with steam."
                    )
                    continue
            else:
                valid, detail = _validate_original_file(orig)
                if not valid:
                    print(f"[!] Warning: {detail} CRC mismatch (skipping strict validation)")

            bak = self.backup_dir / f
            if not bak.exists():
                shutil.copy(orig, bak)

            org = pkg / f"{f}.org"
            if not org.exists():
                shutil.copy(orig, org)

        self.update_open_mods_visibility()

        self.status_label.setText("Game folder set.")

    def open_mods_folder(self):
        game_dir = self.select_game_dir.text().strip()
        if not game_dir:
            QMessageBox.warning(self, "Error", "Please select a game folder first.")
            return
        mods_path = Path(game_dir) / "mods"
        mods_path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(mods_path)))

    def temp_mods_extracted(self, folder_path):
        mods_folder = folder_path
        zip_names = {_normalize_key(f.stem) for f in mods_folder.glob("*.zip")}

        temp_root = Path.cwd() / "temp_"
        if temp_root.exists():
            for d in temp_root.iterdir():
                if d.is_dir() and d.name.endswith("_extracted"):
                    mod_base = d.name.removesuffix("_extracted")
                    if mod_base not in zip_names:
                        try:
                            shutil.rmtree(d)
                            print(f"[Cleanup] Removed stale temp dir for: {mod_base}")
                        except Exception as e:
                            print(f"[Cleanup] Failed to remove {d}: {e}")


    def clear_temp(self, temp_, temp_drag):
        if temp_.exists() or temp_drag.exists():
            try:
                shutil.rmtree(temp_)
                shutil.rmtree(temp_drag)
                print("[Startup Cleanup] Removed leftover temp_")
            except Exception as e:
                print(f"[Startup Cleanup] Failed: {e}")

    def _load_merge_metadata_for_entry(self, entry: Path, mod_name_stem: str, tmp_extract: Path, registry: dict,
                                    changes_accum: list[tuple[str, dict]]):
        meta_path = REGISTRY_PATH
        existing = registry.get(mod_name_stem, {}) if isinstance(registry.get(mod_name_stem), dict) else {}

        # Find modinfo.json
        modinfo_path = None
        if entry.is_dir():
            p = entry / "modinfo.json"
            if p.exists(): modinfo_path = p
        else:
            # extracted temp
            p = tmp_extract / "modinfo.json"
            if p.exists(): modinfo_path = p
            else:
                pass

        # Build base metadata
        merged = {
            "source_path": str(entry),
            "mod_name": mod_name_stem,
            "updated_at": int(time.time()),
            **existing,
        }

        # New probe
        probe = {
            "modinfo_exists": bool(modinfo_path),
            "modinfo_sha1": _file_sha1(modinfo_path) if modinfo_path else "",
            "modinfo_mtime": _file_mtime(modinfo_path) if modinfo_path else 0,
        }

        previously = {
            "modinfo_sha1": existing.get("_probe_modinfo_sha1", ""),
            "modinfo_mtime": existing.get("_probe_modinfo_mtime", 0),
            "modinfo_exists": existing.get("_probe_modinfo_exists", False),
        }

        # Parse fresh modinfo if present
        fresh = {}
        if modinfo_path and modinfo_path.exists():
            fresh = _safe_json_load(modinfo_path)

            # Only allow integer priority 0..5
            pr = fresh.get("priority", merged.get("priority", 5))
            if isinstance(pr, int) and 0 <= pr <= 5:
                fresh["priority"] = pr
            else:
                fresh.pop("priority", None)
            merged.update(fresh)

        # Record probes
        merged["_probe_modinfo_exists"] = probe["modinfo_exists"]
        merged["_probe_modinfo_sha1"]   = probe["modinfo_sha1"]
        merged["_probe_modinfo_mtime"]  = probe["modinfo_mtime"]

        important_keys = ["mod_name", "author", "version", "description", "priority", "link"]
        field_diffs = _diff_fields(existing, merged, important_keys)

        source_changed = (
            probe["modinfo_exists"] != previously["modinfo_exists"]
            or (probe["modinfo_exists"] and probe["modinfo_sha1"] != previously["modinfo_sha1"])
            or (probe["modinfo_exists"] and probe["modinfo_mtime"] != previously["modinfo_mtime"])
        )

        if source_changed or field_diffs:
            # Save and remember to notify
            registry[mod_name_stem] = merged
            if field_diffs:
                changes_accum.append((mod_name_stem, field_diffs))
        else:
            registry[mod_name_stem] = merged

        return merged

    def refresh_list(self):
        # Clear existing tree
        self.mod_list.clear()
        self.clear_temp(temp_, temp_drag)
        
        self.process_mods_folder()
        self.sort_mods_by_priority()

        if FEAT_ACTIVATED_SAVE:
            saved_paths = self.restore_checked_mods()
        else:
            saved_paths = ""
        
        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)

            top_path = top.data(0, Qt.UserRole)
            if top_path and _normpath(str(top_path)) in saved_paths:
                top.setCheckState(0, Qt.Checked)

            for j in range(top.childCount()):
                child = top.child(j)
                child_path = child.data(0, Qt.UserRole)
                if child_path and _normpath(str(child_path)) in saved_paths:
                    child.setCheckState(0, Qt.Checked)

        # self.mod_list.expandAll()
        self.status_label.setText("Mod list refreshed.")

    def process_mods_folder(self):
        game_folder_text = self.select_game_dir.text().strip()
        if not game_folder_text:
            QMessageBox.warning(self, "Warning", "Before initiating any steps,\nmake sure to choose the game folder first.")
            return
        
        mods_folder = Path(game_folder_text) / 'mods'
        mods_folder.mkdir(parents=True, exist_ok=True)

        self.temp_mods_extracted(mods_folder)

        local_temp_root = Path.cwd() / "temp_"
        local_temp_root.mkdir(parents=True, exist_ok=True)

        mods_to_add = []
        meta_path = REGISTRY_PATH
        registry = _load_mod_registry()
        changes_accum = []

        for entry in mods_folder.iterdir():
            if not (entry.is_dir() or entry.suffix.lower() == '.zip'):
                continue

            mod_name_stem = _normalize_key(entry.stem)
            mod_name = _normalize_mod_name(mod_name_stem)
            tmp_extract = local_temp_root / f"{mod_name_stem}_extracted"

            if entry.is_file() and entry.suffix.lower() == '.zip':
                needs_extract = not tmp_extract.exists() or not REGISTRY_PATH.exists()
                if needs_extract:
                    if tmp_extract.exists():
                        shutil.rmtree(tmp_extract)
                    tmp_extract.mkdir()
                    try:
                        with ZipFile(entry) as z:
                            z.extractall(tmp_extract)
                    except Exception as e:
                        print(f"[!] Failed to extract ZIP {entry.name}: {e}")
                        continue

            metadata = self._load_merge_metadata_for_entry(entry, mod_name_stem, tmp_extract, registry, changes_accum)

            priority = metadata.get("priority", 5)
            mods_to_add.append((priority, entry, mod_name, metadata))

        try:
            _write_json_atomic(REGISTRY_PATH, registry)
        except Exception as e:
            print(f"[!] Failed to persist meta.ini: {e}")

        if changes_accum and self.notify_meta_changes.isChecked():
            lines = []
            for name, diffs in changes_accum[:10]:  # cap to avoid huge popups
                lines.append(f"• {_normalize_mod_name(name)}")
                for k, (ov, nv) in diffs.items():
                    limit = 80 if k == "description" else 40
                    lines.append(f"    {k}: {_short(ov, limit)} → {_short(nv, limit)}")
                lines.append("")  # blank line between mods

            more = f"\n…and {len(changes_accum)-10} more." if len(changes_accum) > 10 else ""
            QMessageBox.information(
                self,
                "Metadata updated",
                "Detected changes in mod metadata:\n\n" + "\n".join(lines) + more
            )

        # ascending priority: 0 = highest first
        # mods_to_add.sort(key=lambda x: x[0])  # Use priority range

        # descending priority: 5 = lowest first
        mods_to_add.sort(key=lambda x: x[0])
        
        if FEAT_REGISTRY_META:
            self.prune_mod_meta(meta_path, mods_folder)

        # Now create tree items
        for priority, entry, mod_name, metadata in mods_to_add:
            top = QTreeWidgetItem(self.mod_list, [mod_name])
            # top.setData(0, Qt.UserRole + 2, uid)
            if FEAT_ORDER_UI:
                top.setData(0, Qt.UserRole + 2, priority)
            top.setData(0, Qt.UserRole + 1, mod_name)

            # Tooltip from registry metadata
            if FEAT_REGISTRY_META:
                tip_author = metadata.get("author", "Unknown")
                tip_version = metadata.get("version", "n/a")
                tip_desc = metadata.get("description", "")
                top.setToolTip(0, f"by {tip_author}\nversion {tip_version}\n{tip_desc}")

            top.setFlags(
                top.flags()
                | Qt.ItemIsUserCheckable
                | Qt.ItemIsTristate
                | Qt.ItemIsEnabled
                | Qt.ItemIsSelectable
            )
            top.setCheckState(0, Qt.Unchecked)

            # shared_files child (always included)
            shared = entry / 'shared_files'
            if shared.is_dir():
                sf = QTreeWidgetItem(top, ["shared_files"])
                sf.setCheckState(0, Qt.Checked)
                sf.setData(0, Qt.UserRole, str(shared))
                sf.setFlags(sf.flags() & ~Qt.ItemIsUserCheckable)

            # handle directory variants/None-Variants
            if entry.is_dir():
                vars_dir = [d for d in entry.iterdir() if d.is_dir() and _find_mod_images(d) is not None]

                if len(vars_dir) == 1:
                    var = vars_dir[0]
                    top.setData(0, Qt.UserRole, str(var))

                    # Add the single variant as a child
                    child = QTreeWidgetItem(top, [var.name])
                    child.setFlags(
                        child.flags()
                        | Qt.ItemIsUserCheckable
                        | Qt.ItemIsEnabled
                        | Qt.ItemIsSelectable
                    )
                    child.setCheckState(0, Qt.Unchecked)
                    child.setData(0, Qt.UserRole, str(var))
                
                elif len(vars_dir) > 1:
                    for var in vars_dir:
                        child = QTreeWidgetItem(top, [var.name])
                        child.setData(0, Qt.UserRole, str(var))
                        child.setFlags(
                            child.flags()
                            | Qt.ItemIsUserCheckable
                            | Qt.ItemIsEnabled
                            | Qt.ItemIsSelectable
                        )
                        child.setCheckState(0, Qt.Unchecked)
                        top.setData(0, Qt.UserRole, str(entry))

                ## Non-Variant
                else:
                    # Set data on the top-level item itself
                    top.setData(0, Qt.UserRole, str(entry))
                    top.setFlags(
                        top.flags()
                        | Qt.ItemIsUserCheckable
                        | Qt.ItemIsEnabled
                        | Qt.ItemIsSelectable
                    )
                    top.setCheckState(0, Qt.Unchecked)

            else:
                mod_name_stem = _normalize_key(entry.stem)
                tmp_extract = local_temp_root / f"{mod_name_stem}_extracted"

                # Extract only if not cached
                needs_extract = not tmp_extract.exists() or not meta_path.exists()

                if needs_extract:
                    if tmp_extract.exists():
                        shutil.rmtree(tmp_extract)
                    tmp_extract.mkdir()
                    try:
                        with ZipFile(entry) as z:
                            z.extractall(tmp_extract)
                    except Exception as e:
                        print(f"[!] Failed to extract ZIP {entry.name}: {e}")
                        return
                    
                metadata = self.parse_mod_info(entry, mod_name_stem, tmp_extract, meta_path)
                priority = metadata.get("priority", 5)
                if FEAT_ORDER_UI:
                    top.setData(0, Qt.UserRole + 2, priority)
                
                # Read extracted content as a mod folder
                vars_dir = [d for d in tmp_extract.iterdir() if d.is_dir() and _find_mod_images(d) is not None]

                if len(vars_dir) > 1:
                    for var in vars_dir:
                        child = QTreeWidgetItem(top, [var.name])
                        child.setData(0, Qt.UserRole, str(var))
                        child.setFlags(child.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        child.setCheckState(0, Qt.Unchecked)
                    top.setData(0, Qt.UserRole, str(tmp_extract))

                elif len(vars_dir) == 1:
                    var = vars_dir[0]
                    child = QTreeWidgetItem(top, [var.name])
                    child.setData(0, Qt.UserRole, str(var))
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    child.setCheckState(0, Qt.Unchecked)
                    top.setData(0, Qt.UserRole, str(tmp_extract))
                
                else:
                    # treat root as non-variant
                    top.setData(0, Qt.UserRole, str(tmp_extract))
                    top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    top.setCheckState(0, Qt.Unchecked)

                top.setToolTip(0, f"by {metadata.get('author', 'Unknown')}\nversion {metadata.get('version', 'n/a')}\n {metadata.get('description', '')}")

        self.update_mod_order_labels()


    # Append/Update meta.ini
    def parse_mod_info(self, entry, mod_name_stem, tmp_extract, meta_path):
        modinfo_path = tmp_extract / "modinfo.json"
        if not modinfo_path.exists():
            modinfo_path = entry / "modinfo.json"

        # Base metadata + defaults
        metadata = {
            "source_path": str(entry),
            "mod_name": mod_name_stem,
            "updated_at": int(time.time()),  # unix ts for quick comparisons
        }

        # Default priority
        prio_default = 5

        # Merge in modinfo.json if present
        if modinfo_path.exists():
            try:
                with open(modinfo_path, "r", encoding="utf-8") as f:
                    modinfo = json.load(f)
                metadata.update(modinfo)

                prio = modinfo.get("priority", prio_default)
                if isinstance(prio, int) and 0 <= prio <= 5:
                    metadata["priority"] = prio
            except Exception as e:
                print(f"[!] Failed to parse modinfo.json in {entry.name}: {e}")

        # Append/Update meta.ini
        try:
            # Load existing registry (if any)
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        registry = json.load(f)
                    if not isinstance(registry, dict):
                        registry = {}
                except Exception:
                    registry = {}
            else:
                registry = {}

            registry[mod_name_stem] = metadata

            # Optional
            registry["_meta"] = {
                "schema": 1,
                "last_write": int(time.time()),
                "count": len([k for k in registry.keys() if k != "_meta"]),
            }

            _write_json_atomic(meta_path, registry)
        except Exception as e:
            print(f"[!] Failed to update meta.ini: {e}")

        return metadata

    def prune_mod_meta(self, meta_path: Path, mods_folder: Path):
        try:
            if not meta_path.exists():
                return
            with open(meta_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
            if not isinstance(registry, dict):
                return

            existing_names = set()
            for entry in mods_folder.iterdir():
                if entry.is_dir() or entry.suffix.lower() == ".zip":
                    existing_names.add(_normalize_key(entry.stem))

            changed = False
            for key in list(registry.keys()):
                if key in ("_meta",):
                    continue
                if key not in existing_names:
                    del registry[key]
                    changed = True

            if changed:
                registry["_meta"]["last_prune"] = int(time.time())
                registry["_meta"]["count"] = len([k for k in registry.keys() if k != "_meta"])
                _write_json_atomic(meta_path, registry)
        except Exception as e:
            print(f"[!] prune_mod_meta failed: {e}")

    def restore_checked_mods(self):
        paths = self.prefs.value("mods/activated_paths", [], type=list)
        return [_normpath(p) for p in paths if p] # Normalize all paths

    def write_activated_list(self, checked_paths: list[str]):
        try:
            norm_paths = [_normpath(p) for p in checked_paths]
            self.prefs.setValue("mods/activated_paths", norm_paths)
            self.prefs.sync()
            print(f"[Saved activated mods] → QSettings ({len(norm_paths)} paths)")
        except Exception as e:
            print(f"[ERROR] Failed to save activated mods: {e}")


    def update_mod_order_labels(self):
        for i in range(self.mod_list.topLevelItemCount()):
            item = self.mod_list.topLevelItem(i)
            raw_name = item.data(0, Qt.UserRole + 1)  # original name

            prio = item.data(0, Qt.UserRole + 2)
            item.setText(0, f"{i+1:02d}. [{prio if prio is not None else 5}] {raw_name}")

    def move_selected_mod_up(self):
        current = self.mod_list.currentItem()
        if not current or current.parent():
            return
        index = self.mod_list.indexOfTopLevelItem(current)
        if index > 0:
            was_expanded = current.isExpanded()

            self.mod_list.takeTopLevelItem(index)
            self.mod_list.insertTopLevelItem(index - 1, current)
            self.mod_list.setCurrentItem(current)
            self.update_mod_order_labels()
            if was_expanded:
                self.mod_list.expandItem(current)

    def move_selected_mod_down(self):
        current = self.mod_list.currentItem()
        if not current or current.parent():
            return
        index = self.mod_list.indexOfTopLevelItem(current)
        if index < self.mod_list.topLevelItemCount() - 1:
            was_expanded = current.isExpanded()

            self.mod_list.takeTopLevelItem(index)
            self.mod_list.insertTopLevelItem(index + 1, current)
            self.mod_list.setCurrentItem(current)
            self.update_mod_order_labels()
            if was_expanded:
                self.mod_list.expandItem(current)

    def sort_mods_by_priority(self):
        # Gather raw data
        mods_to_sort = []
        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)
            mod_path = top.data(0, Qt.UserRole)
            mod_name = top.data(0, Qt.UserRole + 1)

            # default when truly None
            prio = top.data(0, Qt.UserRole + 2)
            priority = prio if prio is not None else 5

            checked = top.checkState(0)
            flags   = top.flags()

            # collect children
            children = []
            for j in range(top.childCount()):
                c = top.child(j)
                children.append((
                    c.text(0),
                    c.data(0, Qt.UserRole),
                    c.checkState(0),
                    c.flags()
                ))

            mods_to_sort.append((priority, mod_path, mod_name, checked, flags, children))

        # Sort ascending (0 = highest)
        mods_to_sort.sort(key=lambda x: x[0], reverse=True)

        # Rebuild the tree
        self.mod_list.clear()
        for priority, mod_path, mod_name, checked, flags, children in mods_to_sort:
            top = QTreeWidgetItem([mod_name])
            top.setData(0, Qt.UserRole,      mod_path)
            top.setData(0, Qt.UserRole + 1,  mod_name)
            if FEAT_ORDER_UI:
                top.setData(0, Qt.UserRole + 2,  priority)
            top.setCheckState(0, checked)
            top.setFlags(flags)
            self.mod_list.addTopLevelItem(top)

            for text, path, chk, fl in children:
                child = QTreeWidgetItem([text])
                child.setData(0, Qt.UserRole, path)
                child.setCheckState(0, chk)
                child.setFlags(fl)
                top.addChild(child)

        # Refresh the “01., 02.” labels
        self.update_mod_order_labels()

        self.status_label.setText("Sorted mods by priority.")

    def save_mod_order(self):
        if not FEAT_ORDER_UI:
            return

        order = []
        for i in range(self.mod_list.topLevelItemCount()):
            item = self.mod_list.topLevelItem(i)
            path = item.data(0, Qt.UserRole)
            if path:
                order.append(str(path))

        try:
            self.prefs.setValue("mods/order", order)
            self.prefs.sync()
            self.status_label.setText("Mod order saved.")
        except Exception as e:
            QMessageBox.warning(self, "Save Order Failed", f"Could not save mod order:\n{e}")

    def apply_saved_mod_order(self):
        if not FEAT_ORDER_UI:
            return

        saved = self.prefs.value("mods/order", [], type=list)
        if not saved:
            return

        # Extract all current items into a list of data tuples
        mods = []
        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)
            path = top.data(0, Qt.UserRole)
            prio = top.data(0, Qt.UserRole + 2)
            name = top.data(0, Qt.UserRole + 1)
            checked = top.checkState(0)
            flags = top.flags()
            children = []
            for j in range(top.childCount()):
                c = top.child(j)
                children.append((
                    c.text(0),
                    c.data(0, Qt.UserRole),
                    c.checkState(0),
                    c.flags()
                ))
            mods.append((path, prio, name, checked, flags, children))

        # Build new ordered list using saved list
        path_map = { str(path): (path, prio, name, checked, flags, children)
                    for path, prio, name, checked, flags, children in mods }
        ordered = []
        for p in saved:
            norm = os.path.normcase(os.path.normpath(p))
            for key in list(path_map):
                if os.path.normcase(os.path.normpath(key)) == norm:
                    ordered.append(path_map.pop(key))
                    break
        ordered.extend(path_map.values())

        # Rebuild the tree
        self.mod_list.clear()
        for path, prio, name, checked, flags, children in ordered:
            top = QTreeWidgetItem([name])
            top.setData(0, Qt.UserRole, path)
            top.setData(0, Qt.UserRole + 1, name)
            top.setData(0, Qt.UserRole + 2, prio)
            top.setFlags(flags)
            top.setCheckState(0, checked)
            self.mod_list.addTopLevelItem(top)
            for text, cpath, chk, cflags in children:
                c = QTreeWidgetItem([text])
                c.setData(0, Qt.UserRole, cpath)
                c.setFlags(cflags)
                c.setCheckState(0, chk)
                top.addChild(c)

        self.update_mod_order_labels()


    def on_mod_selected(self, current: QTreeWidgetItem, previous: QTreeWidgetItem):
        self.image_label2.clear()
        if current is None:
            return

        # Grab path from this item or its parent
        path_str = current.data(0, Qt.UserRole)
        if not path_str and current.parent():
            path_str = current.parent().data(0, Qt.UserRole)
        if not path_str:
            return

        mod_folder = Path(path_str)
        img_path = _find_mod_images(mod_folder)

        if img_path and img_path.exists():
            # pix = QPixmap(str(img_path))
            pix = _load_pix(img_path)
            self.image_label2.setPixmap(
                pix.scaled(
                    self.image_label2.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
            )

        # Load metadata registry first then fallback
        registry = _load_mod_registry()

        # The top-level item stores the original (unprefixed) name in UserRole+1
        top_item = current if current.parent() is None else current.parent()
        raw_name = top_item.data(0, Qt.UserRole + 1) or top_item.text(0)
        mod_key = _normalize_key(raw_name)

        metadata = registry.get(mod_key, None)

        if not isinstance(metadata, dict):
            meta_file = mod_folder.parent / "modinfo.json"
            if not meta_file.exists():
                variant_meta = mod_folder / "modinfo.json"
                if variant_meta.exists():
                    meta_file = variant_meta

            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        metadata = json.load(f)
                except Exception as e:
                    print(f"[!] Could not read {meta_file}: {e}")
                    metadata = {}
            else:
                metadata = {}


        # Populate the labels (fall back to defaults)
        self.meta_lbl.setWordWrap(True)
        self.meta_lbl.setText(metadata.get("mod_name", current.text(0)))
        self.meta_author.setText(f"by {metadata.get('author', '')}")
        self.meta_version.setText(f"version {metadata.get('version', '')}")
        self.meta_notes.setWordWrap(True)
        self.meta_notes.setText(metadata.get("description", "Lazy mod maker here (They didnt include the modinfo file!) :)"))

        link = metadata.get("link", "")
        author = metadata.get("author", "")
        self.meta_link.setText(f"Please visit  <a href='{link}'>{author}</a> on Nexus Mods.")

    def update_open_mods_visibility(self):
        game_path = Path(self.select_game_dir.text().strip())
        if game_path.is_dir() and (game_path / "LocalCacheWinGame").exists():
            self.btn_open_mods.show()
        else:
            self.btn_open_mods.hide()

    
    def check_all(self):
        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)
            top.setCheckState(0, Qt.Checked)
            self.status_label.setText("All mods checked.")

    def uncheck_all(self):
        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)
            top.setCheckState(0, Qt.Unchecked)
        self.status_label.setText("All mods unchecked.")


    def remove_selected(self):
        game_dir = Path(self.select_game_dir.text().strip())
        if not game_dir:
            QMessageBox.warning(self, "Error", "Please select a game folder first.")
            return

        mods_dir = game_dir / "mods"
        current  = self.mod_list.currentItem()
        if not current:
            return

        def _safe_remove(p: Path):
            try:
                if p.is_file() or p.suffix.lower() == ".zip" or p.is_symlink():
                    p.unlink(missing_ok=True)
                elif p.exists():
                    shutil.rmtree(p)
            except Exception as e:
                print(f"[Delete] Failed to delete {p}: {e}")

        parent = current.parent()
        try:
            if parent is None:
                # Delete whole mod
                display_name = current.data(0, Qt.UserRole + 1) or current.text(0)
                roots = _candidate_roots(mods_dir, str(display_name), current)

                # Remove dirs/zips
                for r in roots:
                    if r.is_dir():
                        _safe_remove(r)
                    zips = list(mods_dir.glob("*.zip"))
                    for z in zips:
                        if _normalize_mod_name(_normalize_key(z.stem)) == _normalize_mod_name(_normalize_key(str(display_name))):
                            _safe_remove(z)

                # Remove from tree
                idx = self.mod_list.indexOfTopLevelItem(current)
                if idx != -1:
                    self.mod_list.takeTopLevelItem(idx)

                try:
                    save_file = Path.cwd() / "activated.list"
                    if save_file.exists():
                        with open(save_file, "r", encoding="utf-8") as f:
                            paths = [line.strip() for line in f if line.strip()]

                        removed_prefixes = set(_normpath(str(r)) for r in roots if r.is_dir())
                        kept = []
                        for p in paths:
                            np = _normpath(p)
                            keep = True
                            for pref in removed_prefixes:
                                if np.startswith(pref):
                                    keep = False
                                    break
                            if keep:
                                kept.append(p)

                        with open(save_file, "w", encoding="utf-8") as out:
                            for p in kept:
                                out.write(p + "\n")
                except Exception as e:
                    print(f"[activated.list] cleanup failed: {e}")

                removed_text = str(display_name)

            else:
                # Delete a single variant
                top = parent
                display_name = top.data(0, Qt.UserRole + 1) or top.text(0)
                var_name = current.text(0)

                roots = _candidate_roots(mods_dir, str(display_name), top)
                for r in roots:
                    var_folder = r / var_name
                    if var_folder.exists():
                        _safe_remove(var_folder)

                # Remove from UI
                parent.removeChild(current)
                removed_text = f"{display_name}/{var_name}"

                try:
                    save_file = Path.cwd() / "activated.list"
                    if save_file.exists():
                        with open(save_file, "r", encoding="utf-8") as f:
                            paths = [line.strip() for line in f if line.strip()]
                        removable = set(_normpath(str(r / var_name)) for r in roots)
                        kept = [p for p in paths if _normpath(p) not in removable]
                        with open(save_file, "w", encoding="utf-8") as out:
                            for p in kept:
                                out.write(p + "\n")
                except Exception as e:
                    print(f"[activated.list] variant cleanup failed: {e}")

            # Tidy meta and refresh
            try:
                self.prune_mod_meta(REGISTRY_PATH, mods_dir)
            except Exception as e:
                print(f"[meta.ini] prune failed: {e}")

            self.refresh_list()
            self.status_label.setText(f"Removed: {removed_text}")

        except Exception as e:
            print(f"[Remove] Unexpected error: {e}")
            QMessageBox.critical(self, "Remove Failed", str(e))


    def pack_mods(self):
        self.status_label.setText("Packing...")

        if self.conflict_check.isChecked():
            conflict_files = sorted(self.check_conflicts())
            if conflict_files:
                msg = "Conflicts detected in selected mods:\n\n- " + "\n- ".join(conflict_files[:10])  # Show up to 10
                if len(conflict_files) > 10:
                    msg += f"\n...and {len(conflict_files) - 10} more."
                msg += "\n\nDo you want to proceed anyway?"

                reply = QMessageBox.warning(
                    self, "Conflicts Detected", msg,
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if reply == QMessageBox.No:
                    self.status_label.setText("Packing cancelled due to conflicts.")
                    return

        self.thread = QThread()
        self.worker = PackingWorker(self)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.worker.finished.connect(self.on_pack_finished)

        # show modal spinner
        self.spinner_dialog = QProgressDialog("Packing mods...", None, 0, 0, self)
        self.spinner_dialog.setWindowTitle("Please Wait")
        self.spinner_dialog.setWindowModality(Qt.WindowModal)
        self.spinner_dialog.setCancelButton(None)
        self.spinner_dialog.setMinimumDuration(0)
        self.spinner_dialog.show()

        self.thread.start()

    def pack_mods_worker(self) -> tuple[bool, str]:
        try:
            self.restore_default()
        except Exception:
            pass  # ignore errors during automatic restore

        # Validate game folder
        game_folder_text = self.select_game_dir.text().strip()
        if not game_folder_text:
            QMessageBox.warning(self, "Error", "Please select a valid game folder before packing.")
            return False, "No game folder"

        gf = Path(game_folder_text)
        lcache = gf / 'LocalCacheWinGame'
        pkg = lcache / 'package'
        ar = pkg / 'ar'
        ar.mkdir(parents=True, exist_ok=True)

        pack_dir = Path.cwd()
        build_stream_name = 'package.20.00.core.stream'
        build_stream_id = '25'
        temp_inputs = self.temp_dir

        # Collect checked mod paths
        checked_paths = []
        variant_paths = []
        top_paths = []

        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)
            top_path = top.data(0, Qt.UserRole)

            top_has_checked_variant = False
            first_checked_variant_path = None

            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0) == Qt.Checked:
                    child_path = child.data(0, Qt.UserRole)
                    if child_path:
                        variant_paths.append(Path(child_path))
                        first_checked_variant_path = Path(child_path)
                        print(f"[✓] Variant path: {child_path}")
                    top_has_checked_variant = True
                    if FEAT_CONFLICT_COLOR:
                        child.setForeground(0, Qt.magenta)

            if top.checkState(0) == Qt.Checked or top_has_checked_variant:
                if top.checkState(0) == Qt.Checked and top_path:
                    checked_paths.append(top_path)

                if top_path:
                    top_paths.append(Path(top_path))
                    print(f"[✓] Top mod path (from UserRole): {top_path}")
                elif first_checked_variant_path:
                    reconstructed = first_checked_variant_path.parent
                    top_paths.append(reconstructed)
                    print(f"[✓] Top mod path (reconstructed): {reconstructed}")
                else:
                    print("[!] Could not determine top-level path")

                if FEAT_CONFLICT_COLOR:
                    top.setForeground(0, Qt.magenta)

            QApplication.instance().setStyleSheet("QWidget { color: black; }")
                
        # Save checked mod paths
        if FEAT_ACTIVATED_SAVE:
            self.write_activated_list(checked_paths)

        # Clear/create temp
        if temp_inputs.exists():
            shutil.rmtree(temp_inputs)
        temp_inputs.mkdir(parents=True, exist_ok=True)

        # Collect files
        collected = False
        collected |= self.collect_from_variants(variant_paths, temp_inputs)
        collected |= self.collect_top_level_streams(top_paths, temp_inputs)
        for path in top_paths:
            if path.suffix.lower() == '.zip':
                collected |= self.collect_from_zip(path, temp_inputs)

        # Restore original .org backups
        for fname in ('streaming_graph.core', 'streaming_links.stream'):
            backup_file = self.backup_dir / fname
            if backup_file.exists():
                shutil.copy(backup_file, pkg / f"{fname}.org")

        # Pack using Decima_pack.exe
        if collected:
            exe = self.pack_tool
            out_file = pack_dir / build_stream_name
            cmd = [str(exe), str(out_file), build_stream_id]

            if not exe.exists():
                QMessageBox.critical(self, "Error", f"Pack tool not found:\n{exe}")
                return False, "Missing pack tool"

            self.status_label.setText("Packing…")
            try:
                subprocess.run(cmd, cwd=pack_dir, check=True)
            except Exception as e:
                print(f"Pack tool error (ignored): {e}")

            self.status_label.setText("Finalizing…")
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if out_file.exists():
                    break
                QCoreApplication.processEvents()
                time.sleep(0.05)
            else:
                QMessageBox.critical(self, "Error", f"Output not found:\n{out_file.name}")
                return False, "Output not found"

            try:
                shutil.copy(out_file, ar / out_file.name)
                self.status_label.setText("Done.")
                file_count = len(list(temp_inputs.glob("*.stream")) + list(temp_inputs.glob("*.core")))
                return True, f"-- Mod pack created: {out_file.name}\n-- {file_count} files included"
            except Exception as copy_exc:
                QMessageBox.critical(self, "Error", f"Failed to copy:\n{copy_exc}")
                return False, "Copy failed"
        else:
            self.status_label.setText("No eligible files.")
            return False, "No eligible files"

    def restore_default(self):
        gf = Path(self.select_game_dir.text().strip())
        pkg = gf / 'LocalCacheWinGame' / 'package'
        # restore individual core files
        for fname in ['streaming_graph.core', 'streaming_links.stream']:
            backup_file = self.backup_dir / fname
            dest_file = pkg / fname
            try:
                if backup_file.exists():
                    shutil.copy(backup_file, dest_file)
            except PermissionError as pe:
                QMessageBox.warning(
                    self, "Permission Error",
                    f"Could not restore {fname} due to permission error: {pe}\n\n"
                    "Try closing the game or running the app with elevated permissions."
                )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Unexpected Error",
                    f"An unexpected error occurred while restoring '{fname}':\n{e}"
                )

        # clear any existing modded archives in pkg/ar
        ar_dir = pkg / 'ar'
        if ar_dir.exists() and ar_dir.is_dir():
            for item in ar_dir.iterdir():
                try:
                    if item.is_file() or item.is_symlink():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                except Exception as e:
                    print(f"Error deleting {item}: {e}")
        self.status_label.setText("Restored game files and cleared pack mods.")


    def check_conflicts(self):
        IGNORED_EXTENSIONS = { '.png', '.jpg', '.jpeg','.bmp', '.gif', '.txt', '.md', '.ini', '.json' }

        # Build a map of filename → count
        name_counts = {}
        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0) != Qt.Checked:
                    continue
                child_data = child.data(0, Qt.UserRole)
                child_path = Path(child_data) if isinstance(child_data, (str, os.PathLike)) else None
                if not child_path:
                    continue
                for f in child_path.rglob('*'):
                    if f.is_file() and f.suffix.lower() not in IGNORED_EXTENSIONS:
                        name_counts[f.name] = name_counts.get(f.name, 0) + 1

        # Detect and color conflicts
        conflicts = set()
        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)
            conflict_in_top = False

            for j in range(top.childCount()):
                child = top.child(j)
                child_data = child.data(0, Qt.UserRole)
                child_path = Path(child_data) if isinstance(child_data, (str, os.PathLike)) else None
                conflict = False

                if child.checkState(0) == Qt.Checked and child_path:
                    for f in child_path.rglob('*'):
                        if f.is_file() and f.suffix.lower() not in IGNORED_EXTENSIONS:
                            if name_counts.get(f.name, 0) > 1:
                                conflict = True
                                conflicts.add(f.name)
                                break

                # child.setForeground(0, Qt.yellow if conflict else test)
                child.setForeground(0, Qt.red)

                if conflict:
                    conflict_in_top = True

            if conflict_in_top:
                top.setForeground(0, Qt.red)
            # top.setForeground(0, Qt.yellow if conflict_in_top else test)

        return list(conflicts)
    
    def collect_from_variants(self, variant_paths: list[Path], temp_dir: Path) -> bool:
        collected = False
        for f in variant_paths:
            if f.is_dir():
                for file in f.rglob('*'):
                    if file.suffix.lower() in ('.stream', '.core'):
                        shutil.copy(file, temp_dir / file.name)
                        collected = True
                        print(f"[Variant] Collected: {file}")
        return collected

    def collect_top_level_streams(self, mod_paths: list[Path], temp_dir: Path) -> bool:
        collected = False
        for mod_path in mod_paths:
            if mod_path.is_dir():
                for f in mod_path.iterdir():
                    if f.suffix.lower() in ('.stream', '.core'):
                    # if (
                    #     (f.suffix.lower() == '.stream' and '_' in f.stem and f.stem.endswith(('mesh', 'texture')))
                    #     or (f.suffix.lower() == '.core' and all(c in '0123456789ABCDEFabcdef' for c in _normalize_key(f.stem)))
                    # ):
                        shutil.copy(f, temp_dir / f.name)
                        print(f"[Top-Level] Collected: {f.name}")
                        
                        collected = True
        return collected

    def collect_from_zip(self, path: Path, temp_dir: Path) -> bool:
        collected = False
        try:
            with ZipFile(path) as z:
                for info in z.infolist():
                    name = Path(info.filename).name
                    stem = Path(name).stem
                    ext  = Path(name).suffix.lower()

                    is_stream = (ext == '.stream' and '_' in stem and stem.endswith(('mesh', 'texture')))
                    is_core   = (ext == '.core' and all(c in '0123456789abcdefABCDEF' for c in _normalize_key(stem)))

                    if is_stream or is_core:
                        # skip directories
                        if info.is_dir():
                            continue
                        with z.open(info) as src, open(temp_dir / name, 'wb') as dst:
                            dst.write(src.read())
                        print(f"[Zip] Extracted: {name}")
                        collected = True
        except BadZipFile:
            QMessageBox.warning(self, "Warning", f"Invalid zip: {path.name}")
        return collected


    def on_pack_finished(self, success: bool, message: str):
        self.spinner_dialog.close()
        if success:
            QMessageBox.information(self, "Packing Complete", message)
            self.status_label.setText("Done.")
        else:
            QMessageBox.critical(self, "Error", message)
            self.status_label.setText("Packing failed.")


class StreamPacking(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.exe_path = Path.cwd() / "h2_pc_mi_07.exe"
        self.init_ui()

    def init_ui(self):
        frame = QFrame(self)
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setFrameShadow(QFrame.Raised)

        form = QFormLayout(frame)
        form.setContentsMargins(12, 12, 12, 12)

        self.group_id = QLineEdit()
        self.group_id.setPlaceholderText("12a4b")
        self.group_id.setFixedWidth(200)
        # browse_group = QPushButton("…")
        # browse_group.clicked.connect(self.on_browse_group)
        hl0 = QHBoxLayout()
        hl0.addWidget(self.group_id)
        # hl0.addWidget(browse_group)
        form.addRow("Group ID*:", hl0)

        self.lod = QSpinBox()
        self.lod.setRange(0, 10)
        self.lod.setFixedWidth(200)
        form.addRow("LOD Level:", self.lod)

        self.mesh_id = QLineEdit()
        self.mesh_id.setPlaceholderText("888")
        self.mesh_id.setFixedWidth(200)
        form.addRow("Mesh ID:", self.mesh_id)

        self.src_skel = QLineEdit()
        self.src_skel.setPlaceholderText("source skeleton .ascii file")
        browse_skel = QPushButton("Browse…")
        browse_skel.clicked.connect(self.on_browse_skeleton)
        hl1 = QHBoxLayout()
        hl1.addWidget(self.src_skel)
        hl1.addWidget(browse_skel)
        form.addRow("Source Skeleton:", hl1)

        self.new_mesh = QLineEdit()
        self.new_mesh.setPlaceholderText("new mesh .ascii file")
        browse_mesh = QPushButton("Browse…")
        browse_mesh.clicked.connect(self.on_browse_new_mesh)
        hl2 = QHBoxLayout()
        hl2.addWidget(self.new_mesh)
        hl2.addWidget(browse_mesh)
        form.addRow("New Mesh:", hl2)

        self.texture_id = QLineEdit()
        self.texture_id.setPlaceholderText("c8")
        self.texture_id.setFixedWidth(200)
        form.addRow("Texture ID:", self.texture_id)

        self.new_texture = QLineEdit()
        self.new_texture.setPlaceholderText("new texture .dds file")
        browse_tex = QPushButton("Browse…")
        browse_tex.clicked.connect(self.on_browse_new_texture)
        hl3 = QHBoxLayout()
        hl3.addWidget(self.new_texture)
        hl3.addWidget(browse_tex)
        form.addRow("New Texture:", hl3)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.mesh_btn = QPushButton(".ascii to .stream")
        self.mesh_btn.setFixedSize(120, 60)
        self.mesh_btn.clicked.connect(self.pack_mesh)
        self.tex_btn = QPushButton(".dds to .stream")
        self.tex_btn.setFixedSize(120, 60)
        self.tex_btn.clicked.connect(self.pack_texture)
        btn_layout.addWidget(self.mesh_btn)
        btn_layout.addWidget(self.tex_btn)
        btn_layout.addStretch()
        form.addRow("", btn_layout)

        outer = QVBoxLayout(self)
        outer.addWidget(frame)
        outer.addStretch()

    # Packing helpers as staticmethods
    @staticmethod
    def run_packing_mesh(
        exe_path: str,
        group_id: str,
        lod: int,
        original_skeleton: str,
        new_mesh: str,
        mesh_number: str,
        work_dir: str = ".",
        pack_subdir: str = "mesh_pack",
    ) -> list[str]:
        cmd = [
            str(exe_path),
            group_id,
            str(lod),
            original_skeleton,
            new_mesh,
            mesh_number,
        ]
        return _run_and_copy_core_stream(cmd, work_dir, pack_subdir)

    @staticmethod
    def run_packing_texture(
        exe_path: str,
        group_id: str,
        lod: int,
        new_texture: str,
        texture_number: str,
        work_dir: str = ".",
        pack_subdir: str = "texture_pack",
    ) -> list[str]:
        cmd = [
            str(exe_path),
            group_id,
            str(lod),
            new_texture,
            texture_number,
        ]
        return _run_and_copy_core_stream(cmd, work_dir, pack_subdir)


    # Browse
    def on_browse_group(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file to derive Group ID from (optional)", "", "*.*"
        )
        if path:
            self.group_id.setText(Path(path).stem)

    def on_browse_skeleton(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Skeleton File", "", "Skeleton Files (*.ascii);;All Files (*)"
        )
        if path:
            self.src_skel.setText(path)

    def on_browse_new_mesh(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Mesh File", "", "Mesh Files (*.ascii);;All Files (*)"
        )
        if path:
            self.new_mesh.setText(path)

    def on_browse_new_texture(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Texture File", "", "Image Files (*.dds);;All Files (*)"
        )
        if path:
            self.new_texture.setText(path)


    # Invoke packers
    def pack_mesh(self):
        exe      = self.exe_path
        group    = self.group_id.text().strip()
        mesh     = self.mesh_id.text().strip()
        lod      = self.lod.value()
        src      = self.src_skel.text().strip()
        newm     = self.new_mesh.text().strip()

        # Simple validation
        if not all([group, mesh, src, newm]):
            return QMessageBox.warning(
                self, "Invalid Input", "All fields (Group ID, Mesh ID, Skeleton, New Mesh) must be filled."
            )

        try:
            out = self.run_packing_mesh(
                exe_path=exe,
                group_id=group,
                lod=lod,
                original_skeleton=src,
                new_mesh=newm,
                mesh_number=mesh,
                work_dir="./work",
                pack_subdir="mesh_pack"
            )
        except Exception as e:
            return QMessageBox.critical(self, "Mesh Packing Failed", str(e))

        QMessageBox.information(
            self, "Mesh Packing Complete",
            f"✅ Mesh pack created for group {group}, mesh {mesh}\n\n"
            "Files included:\n" + "\n".join(out)
        )

    def pack_texture(self):
        exe   = self.exe_path
        group = self.group_id.text().strip()
        lod   = self.lod.value()
        newt  = self.new_texture.text().strip()
        tex   = self.texture_id.text().strip()

        if not all([group, tex, newt]):
            return QMessageBox.warning(
                self, "Invalid Input", "All fields (Group ID, Texture ID, New Texture) must be filled."
            )

        try:
            out = self.run_packing_texture(
                exe_path=exe,
                group_id=group,
                lod=lod,
                new_texture=newt,
                texture_number=tex,
                work_dir=".",
                pack_subdir="texture_pack"
            )
        except Exception as e:
            return QMessageBox.critical(self, "Texture Packing Failed", str(e))

        QMessageBox.information(
            self, "Texture Packing Complete",
            f"✅ Texture pack created for group {group}, texture {tex}\n\n"
            "Files included:\n" + "\n".join(out)
        )


class ExternalLinkPage(QWebEnginePage):
    def acceptNavigationRequest(self, url: QUrl, nav_type: QWebEnginePage.NavigationType, isMainFrame: bool):
        # If the user clicked a link, open externally
        if nav_type == QWebEnginePage.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, isMainFrame)

class Help(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        # Load & convert markdown
        md_path = Path.cwd() / "res/info.md"
        raw     = md_path.read_text(encoding='utf-8')
        body    = markdown.markdown(raw, extensions=['fenced_code', 'tables'])

        full_html = f"""<!DOCTYPE html>
<html>
  <head><meta charset="utf-8">
    <style>
      body {{ font-family: sans-serif; padding: 20px; }}
      pre  {{ background: #f5f5f5; padding: 10px; }}
      table{{ border-collapse: collapse; width: 100%; }}
      th, td {{ border: 1px solid #ccc; padding: 5px; }}
    </style>
  </head>
  <body>{body}</body>
</html>"""

        # Set up QWebEngineView + custom page
        view = QWebEngineView(self)
        page = ExternalLinkPage(view)
        view.setPage(page)
        view.setHtml(full_html, QUrl.fromLocalFile(str(md_path.parent) + '/'))

        layout = QVBoxLayout(self)
        layout.addWidget(view)
        self.setLayout(layout)


def main():
    app = QApplication(sys.argv)
    QCoreApplication.setOrganizationName("Julz876")
    QCoreApplication.setApplicationName("HFWModManager")
    # QCoreApplication.setOrganizationDomain("")
    lock_path = os.path.join(tempfile.gettempdir(), "HFW_Mod_Manager.lock")
    lock = QLockFile(lock_path)

    if not lock.tryLock(100):  # wait up to 100 ms
        QMessageBox.critical(
            None,
            "Already Running",
            "Another copy of Horizon FW Mod Manager is already running.\n\n"
            "Please close it before launching a new instance."
        )
        return 1
    app._lock = lock 

    mgr = ModManager()
    mgr.resize(1200, 400)
    mgr.show()
    return app.exec_()

if __name__ == "__main__":
    sys.exit(main())
    
