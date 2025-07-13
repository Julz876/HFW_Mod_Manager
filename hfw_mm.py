import sys, time
import os, re
import shutil
import tempfile
from zipfile import ZipFile, BadZipFile
import subprocess
from pathlib import Path
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *


def normalize_mod_name(raw_name: str) -> str:
    # look for “-<pkg>-<major>-<minor>-<build>” at the end
    m = re.match(r"(.+?)-\d+-(\d+)-(\d+)-\d+$", raw_name)
    if m:
        base, maj, mino = m.groups()
        return f"{base.strip()} v{int(maj)}.{int(mino)}"
    return raw_name


class DropTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setSelectionMode(self.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragDropMode(self.DropOnly)
        self.itemChanged.connect(self._on_item_changed)

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
        # update parent to partially checked to reflect single selection
        parent.setCheckState(0, Qt.PartiallyChecked)
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)
    
    def dropEvent(self, event):
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
            mod_name = normalize_mod_name(path.name)
            display_name = mod_name
            mod_path = path

            # handle multiple-variation folders for directories
            if path.is_dir():
                vars_dir = [d for d in path.iterdir() if d.is_dir() and (d / 'variation.png').exists()]
                if len(vars_dir) > 1:
                    dlg = VariationDialog(mod_name, path, parent=main_win)
                    if dlg.exec_() == QDialog.Accepted:
                        sel = vars_dir[dlg.list_widget.currentRow()]
                        mod_path = sel
                        display_name = f"{mod_name}/{sel.name}"
                    else:
                        continue

            # handle .zip files with variation support
            if path.suffix.lower() == '.zip':
                with ZipFile(path) as z:
                    dirs_with_var = set(info.filename.split('/')[0] for info in z.infolist() if info.filename.endswith('variation.png'))
                if len(dirs_with_var) > 1:
                    # extract only variant folders to a temp dir
                    tmp_root = Path(tempfile.mkdtemp())
                    with ZipFile(path) as z:
                        for info in z.infolist():
                            top_folder = info.filename.split('/')[0]
                            if top_folder in dirs_with_var:
                                z.extract(info, tmp_root)
                    # launch variation dialog on extracted root (contains variant dirs)
                    dlg = VariationDialog(mod_name, tmp_root, parent=main_win)
                    if dlg.exec_() == QDialog.Accepted:
                        sel_name = dlg.list_widget.currentItem().text()
                        mod_path = tmp_root / sel_name
                        display_name = f"{mod_name}/{sel_name}"
                    else:
                        continue
                else:
                    # no variants, extract entire zip to mods folder
                    if mods_folder:
                        target = mods_folder / mod_name
                        if target.exists():
                            shutil.rmtree(target)
                        with ZipFile(path) as z:
                            z.extractall(target)
                        mod_path = target
                        display_name = mod_name
                    mod_path = target
                    display_name = mod_name

            #find or create the top-level node
            top = None
            for i in range(self.topLevelItemCount()):
                if self.topLevelItem(i).text(0) == mod_name:
                    top = self.topLevelItem(i)
                    break
            if not top:
                top = QTreeWidgetItem(self, [mod_name])
                top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsTristate | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                top.setCheckState(0, Qt.Unchecked)

            # skip duplicate children
            dup = any(top.child(j).text(0) == display_name for j in range(top.childCount()))
            if dup:
                continue

            # add variation or mod child
            child = QTreeWidgetItem(top, [display_name])
            child.setFlags(child.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            child.setCheckState(0, Qt.Unchecked)
            child.setData(0, Qt.UserRole, str(mod_path))
            top.addChild(child)

            #——— copy selected mod variation into mods/ repository ———
            if mods_folder and mod_path:
                # prepare destination dirs
                base_target = mods_folder / mod_name
                target = base_target / Path(mod_path).name
                try:
                    # remove existing target if present
                    if target.exists():
                        shutil.rmtree(target)
                    # copy the selected variation/mod folder
                    shutil.copytree(mod_path, target, dirs_exist_ok=True)
                    # also copy shared_files
                    shared_src = path / 'shared_files'
                    if shared_src.is_dir():
                        shutil.copytree(shared_src, base_target / 'shared_files', dirs_exist_ok=True)
                    # also include top-level stream and core files (e.g., _texture.stream, _mesh.stream) from original path or zip
                    if path.is_dir() and not shared_src.exists():
                        root_src = path
                        for f in root_src.iterdir():
                            if f.suffix.lower() in ('.stream', '.core'):
                                shutil.copy(f, base_target / f.name)
                    
                    elif path.suffix.lower() == '.zip':
                        with ZipFile(path) as z:
                            for info in z.infolist():
                                parts = Path(info.filename)
                                if len(parts.parts) == 1 and parts.suffix.lower() in ('.stream', '.core'):
                                    dest = base_target / parts.name
                                    target_parent = dest.parent
                                    target_parent.mkdir(parents=True, exist_ok=True)
                                    with z.open(info) as src, open(dest, 'wb') as dst:
                                        dst.write(src.read())
                except Exception as e:
                    print(f"Error copying {mod_path} to {target}: {e}")

            # include shared_files
            shared = path / 'shared_files'
            if shared.is_dir():
                exists = any(top.child(j).text(0) == 'shared_files' for j in range(top.childCount()))
                if not exists:
                    sf = QTreeWidgetItem(top, ["shared_files"])
                    sf.setCheckState(0, Qt.Checked)
                    sf.setData(0, Qt.UserRole, str(shared))
                    # disable user toggling
                    sf.setFlags(sf.flags() & ~Qt.ItemIsUserCheckable)

        event.acceptProposedAction()


class VariationDialog(QDialog):
    def __init__(self, mod_name, mod_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Variation")
        self.mod_path = Path(mod_path)
        # find variation folders containing variation.png
        self.variations = [d for d in self.mod_path.iterdir() if d.is_dir() and (d / 'variation.png').exists()]
        # main layout
        dlg_layout = QVBoxLayout(self)
        # top label
        top_label = QLabel(
            f'The mod "{mod_name}" contains multiple variations. '
            "Please select the one you'd like to install."
        )
        dlg_layout.addWidget(top_label)
        # split list and image preview
        split = QHBoxLayout()
        # variation list
        self.list_widget = QListWidget()
        for var in self.variations:
            self.list_widget.addItem(var.name)
        self.list_widget.currentRowChanged.connect(self.update_preview)
        split.addWidget(self.list_widget, 1)
        # image viewer
        self.image_label = QLabel()
        self.image_label.setFixedSize(400, 400)
        self.image_label.setAlignment(Qt.AlignCenter)
        split.addWidget(self.image_label, 2)
        dlg_layout.addLayout(split)

        # buttons
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        dlg_layout.addLayout(btn_layout)
        # initialize selection
        if self.variations:
            self.list_widget.setCurrentRow(0)

    def update_preview(self, idx):
        if idx < 0 or idx >= len(self.variations):
            return
        var = self.variations[idx]
        img_path = var / 'variation.png'
        if img_path.exists():
            pix = QPixmap(str(img_path))
            self.image_label.setPixmap(pix.scaled(
                self.image_label.size(), Qt.KeepAspectRatio
            ))


class ModManager(QWidget):
    # CONFIG_PATH = Path(__file__).parent / 'decima.ini'
    CONFIG_PATH = Path.cwd() / 'decima.ini'

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HFW MOD MANAGER by kingjulz")

        icon_path = os.path.join(Path(__file__).parent,'res','hfw_mm_icon_02.png')
        app.setWindowIcon(QIcon(icon_path))

        # Temp workspace for packing
        # self.temp_dir = Path(__file__).parent / 'pack'
        self.temp_dir = Path.cwd() / 'pack'
        self.backup_dir = Path.cwd() / 'backup'
        self.init_ui()
        self.load_config()

    def load_config(self):
        # Read bare cache path from decima.ini (should include LocalCacheWinGame)
        if self.CONFIG_PATH.exists():
            cache_path = Path(self.CONFIG_PATH.read_text().strip())
            # if pointing to cache, derive game folder
            if cache_path.name == 'LocalCacheWinGame':
                game_path = cache_path.parent
                self.select_game_dir.setText(str(game_path))
                self.post_browse_setup(game_path)
            elif cache_path.exists():
                # assume user saved full game path
                self.select_game_dir.setText(str(cache_path))
                self.post_browse_setup(cache_path)

    def save_config(self, game_path: Path):
        # Save cache path including LocalCacheWinGame
        cache_dir = game_path / 'LocalCacheWinGame'
        with open(self.CONFIG_PATH, 'w') as f:
            f.write(str(cache_dir))

    def init_ui(self):
        self.temp_dir.mkdir(exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        main_layout = QVBoxLayout(self)

        # Game selector
        gl = QHBoxLayout()
        self.select_game_dir = QLineEdit(); self.select_game_dir.setPlaceholderText("Select game path...")
        btn_game = QPushButton("Browse"); btn_game.clicked.connect(self.browse_game)
        gl.addWidget(QLabel("Game Folder:")); gl.addWidget(self.select_game_dir); gl.addWidget(btn_game)
        main_layout.addLayout(gl)

        # Split drop list and buttons
        sl = QHBoxLayout()
        left = QFrame(); ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Drag & Drop mods:")); self.mod_list = DropTreeWidget(self); ll.addWidget(self.mod_list)
        sl.addWidget(left, 3)
        right = QFrame(); rl = QVBoxLayout(right)

        about_layout = QVBoxLayout()
        png_path = os.path.join(Path(__file__).parent,'res','hfw_mm_icon_02.png')
        self.newview= QLabel()
        pixmap = QPixmap(png_path)
        self.newview.setFixedSize(200, 200)
        # self.newview.setAlignment(Qt.AlignRight)
        self.newview.setPixmap(pixmap)
        self.newview.setScaledContents(True)
        
        version = QLabel("Version: 0.0.1")

        rl.addWidget(self.newview)
        rl.addWidget(version)
        # sl.addStretch()

        for txt, func, icon_name in [
            ("Check All", self.check_all, 'SP_DialogYesButton'),
            ("Un-Check All", self.uncheck_all, 'SP_DialogNoButton'),
            ("Refresh Mod List", self.refresh_list, 'SP_DialogResetButton'),
            ("Remove Selected Mods", self.remove_selected, 'SP_DialogCancelButton'),
            ("Restore Game Files", self.restore_default, 'SP_DialogOkButton'),
            ("Pack Activated Mods", self.pack_mods, 'SP_DialogSaveButton')
        ]:
            btn = QToolButton()
            btn.setFixedSize(200, 60)
            btn.clicked.connect(func)
            
            sp_constant = getattr(QStyle, icon_name)
            btn.setIcon(self.style().standardIcon(sp_constant))
            btn.setIconSize(QSize(50, 50))
            # btn.setLayoutDirection(Qt.RightToLeft)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setText(txt)

            rl.addWidget(btn)
        rl.addStretch()
        sl.addWidget(right, 1)

        # image viewer
        self.image_label2 = QLabel()
        self.image_label2.setFixedSize(200, 200)
        self.image_label2.setAlignment(Qt.AlignCenter)
        rl.addWidget(self.image_label2)
        self.mod_list.currentItemChanged.connect(self.on_mod_selected)
        rl.addStretch()

        special_thanks = "Special Thanks:" + "\n- id-daemon, - HardcoreHobbyist\n- hornycopter"
        thanks = QLabel (special_thanks)
        rl.addWidget(thanks)

        sl.addLayout(about_layout)


        main_layout.addLayout(sl)
        self.status_label = QLabel("Status: Idle"); main_layout.addWidget(self.status_label)

    def on_mod_selected(self, current: QTreeWidgetItem, previous: QTreeWidgetItem):
        if current is None:
            self.image_label2.clear()
            return
        
        # pull the path out of column 0, Qt.UserRole
        path_str = current.data(0, Qt.UserRole)
        if not path_str:
            self.image_label2.clear()
            return

        mod_path = Path(path_str)
        preview_file = mod_path / 'variation.png'
        if preview_file.exists():
            pix = QPixmap(str(preview_file))
            self.image_label2.setPixmap(
                pix.scaled(self.image_label2.size(), Qt.KeepAspectRatio)
            )
        else:
            self.image_label2.clear()

    def browse_game(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Game Folder")
        if not folder:
            return
        game_path = Path(folder)
        self.select_game_dir.setText(str(game_path))
        # save config
        self.save_config(game_path)
        # common post-browse setup
        self.post_browse_setup(game_path)

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
            bak = self.backup_dir / f
            if orig.exists() and not bak.exists():
                shutil.copy(orig, bak)
            org = pkg / f"{f}.org"
            if orig.exists() and not org.exists():
                shutil.copy(orig, org)
        self.status_label.setText("Game folder set.")

    def refresh_list(self):
        game_folder_text = self.select_game_dir.text().strip()
        if not game_folder_text:
            QMessageBox.warning(self, "Error", "Select a game folder first to refresh mods.")
            return
        
        mods_folder = Path(game_folder_text) / 'mods'
        mods_folder.mkdir(parents=True, exist_ok=True)

        # Clear existing tree
        self.mod_list.clear()

        # Rebuild tree with proper children
        for entry in mods_folder.iterdir():
            if not (entry.is_dir() or entry.suffix.lower() == '.zip'):
                continue

            mod_name = normalize_mod_name(entry.name)
            # ensure versions like "v1" become "v1.0" on refresh
            top = QTreeWidgetItem(self.mod_list, [mod_name])

            top.setFlags(top.flags()
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

            # handle directory variants
            if entry.is_dir():
                vars_dir = [d for d in entry.iterdir() if (d / 'variation.png').exists()]
                if len(vars_dir) > 1:
                    for var in vars_dir:
                        child = QTreeWidgetItem(top, [var.name])
                        child.setFlags(
                            child.flags()
                            | Qt.ItemIsUserCheckable
                            | Qt.ItemIsEnabled
                            | Qt.ItemIsSelectable
                        )
                        child.setCheckState(0, Qt.Unchecked)
                        child.setData(0, Qt.UserRole, str(var))
                elif len(vars_dir) == 1:
                    var = vars_dir[0]
                    child = QTreeWidgetItem(top, [var.name])
                    child.setFlags(
                        child.flags()
                        | Qt.ItemIsUserCheckable
                        | Qt.ItemIsEnabled
                        | Qt.ItemIsSelectable
                    )
                    child.setCheckState(0, Qt.Unchecked)
                    child.setData(0, Qt.UserRole, str(var))
                else:
                    child = QTreeWidgetItem(top, [mod_name])
                    child.setFlags(
                        child.flags()
                        | Qt.ItemIsUserCheckable
                        | Qt.ItemIsEnabled
                        | Qt.ItemIsSelectable
                    )
                    child.setCheckState(0, Qt.Unchecked)
                    child.setData(0, Qt.UserRole, str(entry))
            else:
                # treat zip as a single child
                child = QTreeWidgetItem(top, [mod_name])
                child.setFlags(
                    child.flags()
                    | Qt.ItemIsUserCheckable
                    | Qt.ItemIsEnabled
                    | Qt.ItemIsSelectable
                )
                child.setCheckState(0, Qt.Unchecked)
                child.setData(0, Qt.UserRole, str(entry))

        # expand all so that users immediately see variants
        self.mod_list.expandAll()
        self.status_label.setText("Mod list refreshed.")

    def remove_selected(self):
        # Get the currently highlighted item in the tree
        current = self.mod_list.currentItem()
        if current is None:
            return

        parent = current.parent()
        if parent is None:
            # Top-level node: remove the entire mod (all its variations)
            index = self.mod_list.indexOfTopLevelItem(current)
            if index != -1:
                self.mod_list.takeTopLevelItem(index)
        else:
            # Child node: remove just this variation/shared_files entry
            parent.removeChild(current)

        self.status_label.setText(f"Removed: {current.text(0)}")

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
                QMessageBox.warning(self, "Warning", f"Could not restore {fname} due to permission error: {pe} Maybe try closing the game and try again.")

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
        # 1) Build a map of filename → count among all checked variations/shared_files
        name_counts = {}
        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0) != Qt.Checked:
                    continue
                child_path = Path(child.data(0, Qt.UserRole))
                for f in child_path.rglob('*'):
                    if f.is_file():
                        name_counts[f.name] = name_counts.get(f.name, 0) + 1

        # 2) Color any child item that participates in a conflict
        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)
            conflict_in_top = False

            for j in range(top.childCount()):
                child = top.child(j)
                child_path = Path(child.data(0, Qt.UserRole)) if child.data(0, Qt.UserRole) else None
                conflict = False
                if child.checkState(0) == Qt.Checked and child_path:
                    for f in child_path.rglob('*'):
                        if f.is_file() and name_counts.get(f.name, 0) > 1:
                            conflict = True
                            break
                # color the child
                child.setForeground(0, Qt.red if conflict else Qt.black)
                if conflict:
                    conflict_in_top = True

            # 3) Optionally highlight the parent if any child conflicted
            top.setForeground(0, Qt.red if conflict_in_top else Qt.black)

    def pack_mods(self):
        try:
            self.restore_default()
        except Exception:
            # ignore errors during automatic restore
            pass

        # Check for conflicts first
        self.check_conflicts()

        # Validate game folder
        game_folder_text = self.select_game_dir.text().strip()
        if not game_folder_text:
            QMessageBox.warning(self, "Error", "Please select a valid game folder before packing.")
            return
        gf = Path(game_folder_text)

        lcache = gf / 'LocalCacheWinGame'
        pkg = lcache / 'package'
        ar = pkg / 'ar'
        ar.mkdir(parents=True, exist_ok=True)

        # Prepare temp workspace
        # pack_dir = Path(__file__).parent
        pack_dir = Path.cwd()

        # Package filename
        # build_stream_name = 'package.20.00.core.stream'
        # build_stream_id = 25

        build_stream_name = 'package.20.01.core.stream'
        build_stream_id = '26'

        # Clear temp inputs
        temp_inputs = pack_dir / 'pack'
        if temp_inputs.exists():
            shutil.rmtree(temp_inputs)
        temp_inputs.mkdir(parents=True, exist_ok=True)
        collected = False

        # Gather eligible files into temp_inputs
        for i in range(self.mod_list.topLevelItemCount()):
            top = self.mod_list.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0) == Qt.Checked:
                    path = Path(child.data(0, Qt.UserRole))

            if path.name.lower() == build_stream_name:
                shutil.copy(Path.cwd(), ar / path.name)
                continue

            if path.is_dir():
                # copy specific streams/cores
                for f in path.rglob('*'):
                    if ((f.suffix.lower() == '.stream' and '_' in f.stem and f.stem.endswith(('mesh','texture'))) or
                        (f.suffix.lower() == '.core' and all(c in '0123456789ABCDEFabcdef' for c in f.stem.replace('_','')))):
                        shutil.copy(f, temp_inputs / f.name)
                        collected = True
            elif path.suffix.lower() == '.zip':
                try:
                    with ZipFile(path) as z:
                        # first check nested package stream
                        for info in z.infolist():
                            if Path(info.filename).name.lower() == build_stream_name:
                                with z.open(info) as src, open(ar / Path(info.filename).name, 'wb') as dst:
                                    dst.write(src.read())
                                collected = True
                                break
                        else:
                            # extract eligible files
                            for info in z.infolist():
                                nm = Path(info.filename).name
                                stem = Path(nm).stem
                                ext = Path(nm).suffix.lower()
                                if ((ext == '.stream' and '_' in stem and stem.endswith(('mesh','texture'))) or
                                    (ext == '.core' and all(c in '0123456789ABCDEFabcdef' for c in stem.replace('_','')))):
                                    with z.open(info) as src, open(temp_inputs / nm, 'wb') as dst:
                                        dst.write(src.read())
                                    collected = True
                except BadZipFile:
                    QMessageBox.warning(self, "Warning", f"Invalid zip: {path.name}")

        # Restore .org backups
        for fname in ('streaming_graph.core', 'streaming_links.stream'):
            backup_file = self.backup_dir / fname
            if backup_file.exists():
                shutil.copy(backup_file, pkg / f"{fname}.org")

        # Run pack tool if inputs present
        if collected:
            exe = pack_dir / 'Decima_pack.exe'
            if not exe.exists():
                QMessageBox.critical(self, "Error", f"Pack tool not found:\n{exe}")
                return

            out_file = pack_dir / build_stream_name
            cmd = [str(exe), str(out_file), build_stream_id]

            # 1) Start packing
            self.status_label.setText("Packing…")
            try:
                subprocess.run(cmd, cwd=pack_dir, check=True)
            except Exception as e:
                print(f"Pack tool error (ignored): {e}")

            # 2) Poll for the output file (timeout after ~2s)
            self.status_label.setText("Finalizing…")
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if out_file.is_file():
                    break
                QCoreApplication.processEvents()  # keep UI responsive
                time.sleep(0.05)
            else:
                QMessageBox.critical(self, "Error", f"Output not found:\n{out_file.name}")
                return

            # 3) Copy—and finish
            target = ar / out_file.name
            try:
                shutil.copy(str(out_file), str(target))
                self.status_label.setText("Done.")
            except Exception as copy_exc:
                QMessageBox.critical(self, "Error", f"Failed to copy:\n{copy_exc}")
        else:
            self.status_label.setText("No eligible files; only copy done.")


if __name__=='__main__':
    app=QApplication(sys.argv); mgr=ModManager(); mgr.resize(1000,600); mgr.show(); sys.exit(app.exec_())
