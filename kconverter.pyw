################################################################################
## Main

import sys
import os
import json
import pickle
import ctypes

from pathlib import Path
from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QSize, QTimer, QFileSystemWatcher
from PyQt6.QtGui import QShortcut, QKeySequence, QIcon, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QTreeWidget, QTreeWidgetItem,
    QFileDialog, QTextEdit, QProgressBar, QDialog, QMessageBox,
    QHeaderView, QLabel, QToolButton
)

from config import (
    VERSION, THUMBNAIL_SIZE, SAVES_DIR,
    PERSISTENT_FILE, PROJECT_EXTENSION, LOCAL_WEBM_FILE,
    ROOT_ROW_HEIGHT,
    FOLDER_ROW_HEIGHT,
    ICON, APPID,
)

from core.conversion.thread import ConversionThread
from core.conversion.image import ImageConverter
from core.conversion.webm import WebMConverter
from core.webm import LocalWebMDialog, normalize_webm_settings
from core.folder import FolderSettings, SettingsDialog
from core.thumbnail import ThumbnailWorker
from core.rescan import RescanWorker


class FolderTree(QTreeWidget):

    folders_dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()

        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)
        self.setColumnCount(2)
        self.setHeaderLabels(["Folder / File", "Status"])
        self.setIconSize(QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 700)


    def dragEnterEvent(self, event):

        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()


    def dragMoveEvent(self, event):

        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()


    def dropEvent(self, event):
        folders = []

        for url in event.mimeData().urls():

            if not url.isLocalFile():
                continue

            path = Path(url.toLocalFile())

            if path.is_dir():
                folders.append(str(path))

        if folders:
            self.folders_dropped.emit(folders)

        event.acceptProposedAction()


    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.position().toPoint())

        if item:
            data = item.data(0, Qt.ItemDataRole.UserRole)

            if data:
                path = Path(data)

                if path.is_file():
                    self.window().open_file(path)
                    event.accept()
                    return

                if path.is_dir():
                    item.setExpanded(not item.isExpanded())
                    event.accept()
                    return

        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    PROJECT_VERSION = 16

    def __init__(self):
        super().__init__()

        self.folders = []
        self.thread = None
        self.thumbnail_worker = None
        self.rescan_worker = None
        self.project_filename = None
        self.project_dirty = False
        self.items_by_path = {}
        self.settings_by_item = {}
        self._tree_state_to_restore = None
        self._rescanning = False
        self._rescan_timer = None
        self._file_status_cache = {}
        # Директории, которые реально изменились.
        self._changed_directories = set()

        # Кеш UI-статусов.
        #
        # key:
        #   (folder, preset identity)
        #
        # value:
        #   {
        #       "source_signature": ...,
        #       "output_size": ...,
        #       "outdated": ...
        #   }
        self._folder_status_cache = {}

        # Кеш содержимого директорий.
        #
        # Нужен, чтобы быстро определить, что именно изменилось,
        # даже если QFileSystemWatcher прислал только directoryChanged.
        self._directory_snapshot = {}

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_watched_directory_changed)
        self._watcher.fileChanged.connect(self._on_watched_file_changed)

        SAVES_DIR.mkdir(parents=True, exist_ok=True)

        self.setWindowTitle("kConverter")
        self.setWindowIcon(QIcon(ICON))
        self.resize(1750, 900)
        self.setup_ui()
        self.setup_menu()
        self.setup_shortcuts()
        self.load_last_project()
        self.update_title()


    def _directory_signature(self, folder):
        """
        Быстрый snapshot директории.

        Мы НЕ читаем содержимое файлов.
        Смотрим только имена, тип и stat файлов.

        Поэтому изменение огромного PNG не приводит к чтению
        мегабайт данных.
        """
        folder = Path(folder).resolve()

        try:
            entries = []

            for entry in folder.iterdir():
                if entry.name.lower() == "backup":
                    continue

                try:
                    stat = entry.stat()

                    entries.append((
                        entry.name,
                        entry.is_dir(),
                        stat.st_mtime_ns,
                        stat.st_size,
                    ))

                except OSError:
                    entries.append((
                        entry.name,
                        entry.is_dir(),
                        None,
                        None,
                    ))

            entries.sort()

            return tuple(entries)

        except OSError:
            return None


    def _register_watch_directories(self):
        """
        Регистрирует все существующие директории проекта.
        """

        wanted = set()

        for settings in self.folders:
            root = Path(settings.source_folder).resolve()

            if not root.is_dir():
                continue

            try:
                for base, dirs, _ in os.walk(root):
                    dirs[:] = [
                        d for d in dirs
                        if d.lower() not in ImageConverter.IGNORE_FOLDERS
                    ]

                    wanted.add(str(Path(base).resolve()))

            except OSError:
                continue

        current = {
            str(Path(path).resolve())
            for path in self._watcher.directories()
        }

        remove = current - wanted

        if remove:
            self._watcher.removePaths(list(remove))

        add = wanted - current

        if add:
            # QFileSystemWatcher может вернуть не все пути,
            # если ОС достигла лимита.
            self._watcher.addPaths(list(add))

        # Обновляем snapshot только для существующих директорий.
        for directory in wanted:

            if directory not in self._directory_snapshot:
                signature = self._directory_signature(directory)

                if signature is not None:
                    self._directory_snapshot[directory] = signature

        # Удаляем старые snapshots.
        for directory in list(self._directory_snapshot):

            if directory not in wanted:
                del self._directory_snapshot[directory]

    def _on_watched_directory_changed(self, path):
        if self._rescanning:
            return

        directory = Path(path).resolve()

        old_signature = self._directory_snapshot.get(str(directory))
        new_signature = self._directory_signature(directory)

        # QFileSystemWatcher иногда может прислать сигнал,
        # хотя фактических изменений нет.
        if old_signature == new_signature:
            return

        if new_signature is None:
            self._changed_directories.add(str(directory.parent))
        else:
            self._changed_directories.add(str(directory))

            self._directory_snapshot[str(directory)] = new_signature

        self._schedule_incremental_rescan()


    def _on_watched_file_changed(self, path):

        if self._rescanning:
            return

        file_path = Path(path).resolve()

        self._changed_directories.add(
            str(file_path.parent)
        )

        self._schedule_incremental_rescan()


    def _schedule_incremental_rescan(self):
        """
        Не делаем rescan непосредственно внутри QFileSystemWatcher.

        При копировании нескольких файлов Windows может прислать
        десятки directoryChanged подряд.

        Поэтому ждём 150 мс и объединяем все изменения в один rescan.
        """

        if self._rescan_timer is None:
            self._rescan_timer = QTimer(self)
            self._rescan_timer.setSingleShot(True)
            self._rescan_timer.timeout.connect(
                self._run_scheduled_rescan
            )

        self._rescan_timer.start(150)


    def _run_scheduled_rescan(self):

        if self._rescanning:
            return

        changed = set(self._changed_directories)
        self._changed_directories.clear()

        if changed:
            self.rescan(changed)


    def _preset_cache_key(self, preset):
        """
        Не используем id(preset), потому что preset может быть
        пересоздан при загрузке проекта.
        """

        return (
            preset.name,
            preset.output_folder,
            preset.suffix,
            preset.resize_mode,
            preset.resolution_width,
            preset.resolution_height,
            preset.downscale,
            preset.target_size,
            preset.max_quality,
            preset.min_quality,
            preset.sharpen_radius,
            preset.sharpen_percent,
            preset.sharpen_threshold,
        )


    def _folder_cache_key(self, folder, preset):
        return str(Path(folder).resolve()), self._preset_cache_key(preset)


    def _image_folder_signature(self, folder):
        folder = Path(folder).resolve()

        entries = []

        try:
            for source in folder.rglob("*.png"):

                if not source.is_file():
                    continue

                if " - " in source.stem:
                    continue

                try:
                    stat = source.stat()

                    entries.append((
                        source.relative_to(folder).as_posix(),
                        stat.st_mtime_ns,
                        stat.st_size,
                    ))

                except OSError:
                    continue

        except OSError:
            return None

        entries.sort()

        return tuple(entries)


    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        top = QHBoxLayout()
        self.rescan_button = QPushButton("")
        self.rescan_button.setIcon(QIcon("icons/rescan.svg"))
        self.rescan_button.clicked.connect(self._full_rescan)
        top.addWidget(self.rescan_button)
        top.addStretch()
        layout.addLayout(top)
        self.tree = FolderTree()
        self.tree.folders_dropped.connect(self.add_folders)
        layout.addWidget(self.tree)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(180)
        layout.addWidget(self.log)
        bottom = QHBoxLayout()
        self.stop_button = QPushButton("STOP")
        self.stop_button.setMinimumWidth(90)
        self.stop_button.setMaximumWidth(105)
        self.stop_button.setMinimumHeight(36)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_conversion)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(24)
        self.progress.setTextVisible(True)
        self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress.setFormat("%v/%m • %p% • Осталось: %1")
        self.progress.setStyleSheet("""
            QProgressBar {
                background-color: #333;
                border: none;
                border-radius: 5px;
                text-align: center;
                color: #fff;
            }

            QProgressBar::chunk {
                background-color: #0BB1C0;
                border-radius: 5px;
            }
        """)
        self.convert_all_button = QPushButton("Convert All")
        self.convert_all_button.setMinimumWidth(120)
        self.convert_all_button.setMinimumHeight(36)
        self.convert_all_button.clicked.connect(self.start_all_conversions)
        bottom.addWidget(self.stop_button)
        bottom.addWidget(self.progress, 1)
        bottom.addWidget(self.convert_all_button)
        layout.addLayout(bottom)


    def setup_menu(self):
        file_menu = self.menuBar().addMenu("File")
        save = file_menu.addAction("Save Project")
        save_as = file_menu.addAction("Save Project As...")
        file_menu.addSeparator()
        load = file_menu.addAction("Load Project...")
        file_menu.addSeparator()
        exit_action = file_menu.addAction("Exit")
        save.triggered.connect(self.save_project)
        save_as.triggered.connect(self.save_project_as)
        load.triggered.connect(self.load_project)
        exit_action.triggered.connect(self.close)


    def setup_shortcuts(self):
        save = QShortcut(QKeySequence("Ctrl+S"), self)
        save.activated.connect(self.save_project)
        save_as = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        save_as.activated.connect(self.save_project_as)


    def update_title(self):
        name = f"{Path(self.project_filename).stem} [{self.project_filename}]" if self.project_filename else "Untitled"

        if self.project_dirty:
            name = "* " + name

        self.setWindowTitle(f'{name} - kConverter {".".join([str(v) for v in VERSION])}')
        self.setWindowIcon(QIcon(ICON))


    def mark_dirty(self):
        self.project_dirty = True
        self.update_title()


    def mark_clean(self):
        self.project_dirty = False
        self.update_title()


    def add_folders(self, folders):
        existing = {settings.source_folder for settings in self.folders}
        added = False

        for folder in folders:
            folder = str(Path(folder).resolve())

            if folder in existing:
                continue

            settings = FolderSettings(folder)
            self.folders.append(settings)
            existing.add(folder)
            added = True
            self.log_message(f"Added: {folder}")

        if added:
            self.mark_dirty()
            self.rescan()


    def create_root_controls(self, settings):
        controls = QWidget()
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        edit_button = QToolButton()
        edit_button.setIcon(QIcon("icons/settings.svg"))
        edit_button.setToolTip("Edit root folder settings")
        edit_button.setAutoRaise(True)
        edit_button.clicked.connect(lambda checked=False, s=settings: self.open_settings(s))
        layout.addWidget(edit_button)
        return controls


    def add_folder_item(self, settings):
        item = QTreeWidgetItem()
        item.setData(0, Qt.ItemDataRole.UserRole, settings.source_folder)
        item.setToolTip(0, settings.source_folder)
        item.setSizeHint(0, QSize(0, ROOT_ROW_HEIGHT))
        self.tree.addTopLevelItem(item)
        self.items_by_path[str(Path(settings.source_folder).resolve())] = item
        self.settings_by_item[id(item)] = settings
        self.tree.setItemWidget(item, 0, self.create_folder_widget(settings, True))
        self.tree.setItemWidget(item, 1, self.create_folder_status(settings, Path(settings.source_folder), True))
        self.populate_tree(item, Path(settings.source_folder), settings)


    def create_folder_widget(self, settings, root=False):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        name = QLabel(Path(settings.source_folder).name)
        font = QFont()
        font.setPointSize(12 if root else 10)
        font.setBold(root)
        name.setFont(font)
        name.setMinimumWidth(180)
        name.setToolTip(settings.source_folder)
        row.addWidget(name)

        if root:
            mode = QLabel(f"  [{settings.mode}]")
            mode_font = QFont()
            mode_font.setPointSize(10)
            mode_font.setBold(True)
            mode.setFont(mode_font)
            row.addWidget(mode)
            row.addWidget(self.create_root_controls(settings))

        row.addStretch()
        return widget


    def populate_tree(self, parent, folder, settings):
        self._populate_folder_children(
            parent,
            Path(folder).resolve(),
            settings,
        )


    def _populate_folder_children(self, parent, folder, settings):
        folder = Path(folder).resolve()

        try:
            entries = sorted(
                folder.iterdir(),
                key=lambda path: (
                    not path.is_dir(),
                    path.name.lower(),
                ),
            )
        except (PermissionError, OSError):
            return

        for entry in entries:

            if entry.is_dir() and entry.name.lower() == "backup":
                continue

            child = QTreeWidgetItem([entry.name])
            child.setData(0, Qt.ItemDataRole.UserRole, str(entry))
            child.setToolTip(0, str(entry))
            child.setSizeHint(0, QSize(0, FOLDER_ROW_HEIGHT))

            parent.addChild(child)

            resolved = str(entry.resolve())

            self.items_by_path[resolved] = child

            if entry.is_dir():
                widget = self.create_folder_status(settings, entry, False)
                self.tree.setItemWidget(child, 1, widget)

                self._populate_folder_children(
                    child,
                    entry,
                    settings,
                )

            elif entry.suffix.lower() in WebMConverter.IMAGE_EXTENSIONS:

                if settings.mode == "Images" and entry.suffix.lower() == ".png":
                    widget = self.create_file_status(settings, entry)
                    self.tree.setItemWidget(child, 1, widget)


    def create_separator(self):
        container = QWidget()
        container.setFixedWidth(14)
        container.setFixedHeight(13)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line = QWidget()
        line.setFixedSize(2, 13)
        line.setStyleSheet("background: #555; border: 0;")
        layout.addWidget(line)
        return container


    def get_folder_thumbnail_source(self, folder):
        folder = Path(folder).resolve()

        try:
            images = sorted((path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in WebMConverter.IMAGE_EXTENSIONS), key=lambda path: path.name.lower())
        except OSError:
            return None

        return images[0] if images else None


    def create_folder_status(self, settings, folder, root=False):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        for index, preset in enumerate(settings.presets):

            if index:
                layout.addWidget(self.create_separator())

            if settings.mode == "WebM":
                local_button = QToolButton()
                has_local = self.has_local_webm_settings(folder, preset)
                local_button.setIcon(QIcon("icons/settings_local.svg") if has_local else QIcon("icons/settings.svg"))
                local_button.setToolTip("Local WebM settings" + (" (override active)" if has_local else ""))
                local_button.setFixedSize(27, 25)
                local_button.clicked.connect(lambda checked=False, s=settings, p=preset, f=Path(folder): self.edit_local_webm(s, p, f))
                layout.addWidget(local_button)

            if settings.mode == "Images":
                size = self.get_folder_output_size(settings, preset, folder)
            else:
                size = self.get_webm_output_size(settings, preset, folder)

            button = QPushButton(preset.name)
            button.setToolTip(f"Convert only this folder using '{preset.name}'")
            button.setFixedHeight(25)
            button.setFixedWidth(75)

            if settings.mode == "Images" and preset.output_folder:
                if self.folder_has_outdated_images(settings, preset, folder):
                    button.setStyleSheet("""
                        QPushButton {
                            color: #ff9800;
                        }
                    """)
                    button.setToolTip(
                        f"Folder contains outdated images.\n"
                        f"Convert only this folder using '{preset.name}'"
                        )

            if not preset.output_folder.strip():
                button.setEnabled(False)
                button.setStyleSheet("QPushButton:disabled { color: #888; }")
                button.setToolTip(f'Preset "{preset.name}" has no Output folder.')
            else:
                button.clicked.connect(lambda checked=False, s=settings, p=preset, f=Path(folder): self.start_conversion(s, p, f))

            layout.addWidget(button)

            if settings.mode == "Images":
                open_button = QToolButton()
                open_button.setIcon(QIcon("icons/folder.svg"))
                open_button.setToolTip("Open converted images folder")
                open_button.setFixedSize(25, 25)
                open_button.clicked.connect(lambda checked=False, s=settings, p=preset, f=Path(folder): self.open_conversion_result(s, p, f))
                layout.addWidget(open_button)
                size_label = QLabel(self.format_size(size))
                size_label.setFixedWidth(75)
                size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(size_label)

            else:
                local = self.get_local_preset(folder, preset)
                webm_output = None

                if preset.output_folder.strip():
                    webm_output = WebMConverter(folder, preset, local).get_output_file()

                if webm_output is not None and webm_output.is_file():
                    webm_size = webm_output.stat().st_size
                    webm_button = QPushButton(self.format_size(webm_size))
                    webm_button.setIcon(QIcon("icons/webm_play.svg"))
                    webm_button.setFixedHeight(25)
                    webm_button.setFixedWidth(75)
                    webm_button.setToolTip(f"Open WebM animation\n{webm_output}")
                    webm_button.clicked.connect(lambda checked=False, s=settings, p=preset, f=Path(folder): self.open_conversion_result(s, p, f))

                else:
                    webm_button = QPushButton("—")
                    webm_button.setFixedHeight(25)
                    webm_button.setFixedWidth(75)
                    webm_button.setEnabled(False)
                    webm_button.setStyleSheet("QPushButton:disabled { color: #888; }")
                    webm_button.setToolTip("WebM file does not exist yet")

                layout.addWidget(webm_button)

        all_button = QPushButton("All")
        all_button.setToolTip("Convert this folder using all presets")
        all_button.setFixedHeight(25)
        all_button.setFixedWidth(75)
        all_button.clicked.connect(lambda checked=False, s=settings, f=Path(folder): self.start_folder_all_conversions(s, f))
        layout.addWidget(self.create_separator())
        layout.addWidget(all_button)
        layout.addStretch()

        return widget


    def create_file_status(self, settings, source):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4 + 108, 0, 4, 0)
        layout.setSpacing(130)

        for preset in settings.presets:
            output, status = self.get_file_status(source, preset)

            button = QPushButton(status)
            button.setFixedHeight(25)
            button.setFixedWidth(75)

            if output is not None and output.exists():
                button.setIcon(QIcon("icons/open_image.svg"))
                button.setToolTip(
                    f"Open converted image: {preset.name}\n{output}"
                )

                if status == "Outdated":
                    button.setStyleSheet("""
                        QPushButton {
                            color: #ff9800;
                        }
                    """)
                    button.setToolTip(
                        f"Image is outdated and needs reconversion: "
                        f"{preset.name}\n{output}"
                    )

                button.clicked.connect(
                    lambda checked=False, path=output: self.open_file(path)
                )

            else:

                if not output:
                    button.setIcon(QIcon("icons/not_configured.svg"))
                button.setEnabled(False)
                button.setStyleSheet(
                    "QPushButton:disabled { color: #888; }"
                )

            layout.addWidget(button)

        layout.addStretch()
        return widget


    def folder_has_outdated_images(self, settings, preset, folder):

        if settings.mode != "Images":
            return False

        if not preset.output_folder.strip():
            return False

        folder = Path(folder).resolve()

        signature = self._image_folder_signature(folder)

        if signature is None:
            return False

        key = self._folder_cache_key(folder, preset)

        cached = self._folder_status_cache.get(key)

        if (
            cached is not None
            and cached.get("signature") == signature
            and "outdated" in cached
        ):
            return cached["outdated"]

        converter = ImageConverter(
            settings.source_folder,
            preset,
            source_root=settings.source_folder,
        )

        outdated = False

        for source in folder.rglob("*.png"):
            if not source.is_file():
                continue

            if " - " in source.stem:
                continue

            try:
                output = converter.get_output_file(source)

                if not output.exists():
                    outdated = True
                    break

                source_stat = source.stat()
                output_stat = output.stat()

                if output_stat.st_mtime_ns < source_stat.st_mtime_ns:
                    outdated = True
                    break

            except OSError:
                outdated = True
                break

        cached = self._folder_status_cache.setdefault(key, {})
        cached["signature"] = signature
        cached["outdated"] = outdated

        return outdated


    def get_folder_output_size(
        self,
        settings,
        preset,
        folder,
    ):
        if not preset.output_folder.strip():
            return 0

        folder = Path(folder).resolve()
        key = self._folder_cache_key(folder, preset)

        signature = self._image_folder_signature(folder)

        if signature is None:
            return 0

        cached = self._folder_status_cache.get(key)

        if (
            cached is not None
            and cached.get("signature") == signature
            and "output_size" in cached
        ):
            return cached["output_size"]

        converter = ImageConverter(
            settings.source_folder,
            preset,
            source_root=settings.source_folder,
        )

        total = 0

        try:
            for source in folder.rglob("*.png"):
                if not source.is_file():
                    continue

                if " - " in source.stem:
                    continue

                try:
                    output = converter.get_output_file(source)

                    if output.exists():
                        total += output.stat().st_size

                except OSError:
                    continue

        except OSError:
            pass

        cached = self._folder_status_cache.setdefault(key, {})

        cached["signature"] = signature
        cached["output_size"] = total

        return total


    def _invalidate_folder_cache(self, folder):
        folder = Path(folder).resolve()

        for key in list(self._folder_status_cache):
            cached_folder = key[0]

            try:
                cached_path = Path(cached_folder)

                cached_path.relative_to(folder)

                del self._folder_status_cache[key]

            except ValueError:
                continue


    def _invalidate_cache_for_tree(self, folder):
        folder = Path(folder).resolve()

        for key in list(self._folder_status_cache):
            cached_folder = Path(key[0])

            try:
                cached_folder.relative_to(folder)
                del self._folder_status_cache[key]
            except ValueError:
                pass


    def get_webm_output_size(self, settings, preset, folder):

        if not preset.output_folder:
            return 0

        local = self.get_local_preset(folder, preset)

        return WebMConverter(folder, preset, local).get_size()


    def format_size(self, size):
        if not size:
            return "—"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        return f"{size / 1024 / 1024 / 1024:.2f} GB"


    def find_settings_for_path(self, path):
        path = Path(path).resolve()

        for settings in self.folders:
            root = Path(settings.source_folder).resolve()

            try:
                path.relative_to(root)
                return settings

            except ValueError:
                continue

        return None


    def get_output_file(self, source, preset):
        settings = self.find_settings_for_path(source)

        if settings is None or not preset.output_folder:
            return None

        return ImageConverter(settings.source_folder, preset).get_output_file(Path(source))


    def get_file_status(self, source, preset):
        source = Path(source).resolve()

        output = self.get_output_file(
            source,
            preset,
        )

        if output is None:
            return None, ""

        try:
            source_stat = source.stat()

            output_stat = (
                output.stat()
                if output.exists()
                else None
            )

        except OSError:
            return output, "Unknown"

        key = (str(source), self._preset_cache_key(preset))

        signature = (
            source_stat.st_mtime_ns,
            source_stat.st_size,
            (
                output_stat.st_mtime_ns,
                output_stat.st_size,
            )
            if output_stat
            else None,
        )

        cached = self._file_status_cache.get(key)

        if cached is not None and cached["signature"] == signature:
            return output, cached["status"]

        if output_stat is None:
            status = "—"

        elif output_stat.st_mtime_ns < source_stat.st_mtime_ns:
            status = "Outdated"

        else:
            status = self.format_size(output_stat.st_size)

        self._file_status_cache[key] = {
            "signature": signature,
            "status": status,
        }

        return output, status


    def _invalidate_file_status_cache(self, folder):
        folder = Path(folder).resolve()

        for key in list(self._file_status_cache):
            source = Path(key[0])

            try:
                source.relative_to(folder)
                del self._file_status_cache[key]

            except ValueError:
                pass


    def get_local_file(self, folder):
        return Path(folder).resolve() / LOCAL_WEBM_FILE


    def read_local_webm_settings(self, folder):
        path = self.get_local_file(folder)

        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)

            return data if isinstance(data, dict) else {}

        except Exception:
            return {}


    def write_local_webm_settings(self, folder, data):
        path = self.get_local_file(folder)

        if not data:
            path.unlink(missing_ok=True)
            return

        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)

        except Exception as exc:
            QMessageBox.critical(self, "Local settings error", str(exc))


    def get_local_preset(self, folder, preset):
        data = self.read_local_webm_settings(folder)
        return data.get(preset.name)


    def has_local_webm_settings(self, folder, preset):
        return self.get_local_preset(folder, preset) is not None


    def edit_local_webm(self, settings, preset, folder):

        if settings.mode != "WebM":
            return

        local_data = self.read_local_webm_settings(folder)
        current = local_data.get(preset.name)
        base = normalize_webm_settings(preset.webm)

        if current:
            base.update(normalize_webm_settings(current))

        base = normalize_webm_settings(base)
        dialog = LocalWebMDialog(folder, preset, base, self)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        if dialog.deleted:
            local_data.pop(preset.name, None)
        else:
            local_data[preset.name] = dialog.settings

        self.write_local_webm_settings(folder, local_data)
        expanded, vertical_scroll, horizontal_scroll = self.get_tree_state()
        self._tree_state_to_restore = {"expanded": expanded, "vertical_scroll": vertical_scroll, "horizontal_scroll": horizontal_scroll}
        self.mark_dirty()
        self.rescan()


    def open_conversion_result(self, settings, preset, folder):
        folder = Path(folder).resolve()

        if settings.mode == "Images":
            output = Path(preset.output_folder)

            if not output.is_absolute():
                output = folder.parent / output

            output = output.resolve()

            if output.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))
            else:
                QMessageBox.information(self, "Output not found", f"Output folder does not exist yet:\n{output}")

        else:
            local = self.get_local_preset(folder, preset)
            output = WebMConverter(folder, preset, local).get_output_file()

            if output.exists():
                self.open_file(output)
            else:
                QMessageBox.information(self, "WebM not found", f"WebM file does not exist yet:\n{output}")


    def start_thumbnail_worker(self):
        paths = []

        for path in self.items_by_path:
            item_path = Path(path)

            if item_path.suffix.lower() in WebMConverter.IMAGE_EXTENSIONS:
                paths.append((path, item_path))
                continue

            if item_path.is_dir():
                item = self.items_by_path.get(path)

                if item is not None and item.parent() is None:
                    continue

                thumbnail_source = self.get_folder_thumbnail_source(item_path)

                if thumbnail_source:
                    paths.append((path, thumbnail_source))

        if not paths:
            return

        if self.thumbnail_worker and self.thumbnail_worker.isRunning():
            self.thumbnail_worker.stop()

        self.thumbnail_worker = ThumbnailWorker(paths)
        self.thumbnail_worker.thumbnail_ready.connect(self.thumbnail_ready)
        self.thumbnail_worker.finished.connect(self.thumbnail_finished)
        self.thumbnail_worker.start()


    def _start_folder_thumbnail_worker(self, folder):
        folder = Path(folder).resolve()
        paths = []

        try:
            for path in folder.iterdir():
                if (
                    path.is_file()
                    and path.suffix.lower()
                    in WebMConverter.IMAGE_EXTENSIONS
                ):
                    paths.append((str(path), path))

        except OSError:
            return

        item = self.items_by_path.get(str(folder))

        if item is not None:
            source = self.get_folder_thumbnail_source(folder)
            if source:
                paths.append((str(folder), source))

        if not paths:
            return

        if self.thumbnail_worker and self.thumbnail_worker.isRunning():
            self.thumbnail_worker.stop()

        self.thumbnail_worker = ThumbnailWorker(paths)
        self.thumbnail_worker.thumbnail_ready.connect(self.thumbnail_ready)
        self.thumbnail_worker.finished.connect(self.thumbnail_finished)
        self.thumbnail_worker.start()


    def thumbnail_ready(self, path, pixmap):
        item = self.items_by_path.get(str(Path(path).resolve()))

        if item is not None:
            item.setIcon(0, QIcon(pixmap))


    def thumbnail_finished(self):
        worker = self.thumbnail_worker
        self.thumbnail_worker = None

        if worker:
            worker.deleteLater()


    def open_file(self, path):
        path = Path(path)

        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


    def open_settings(self, settings):

        dialog = SettingsDialog(settings, self)
        dialog.remove_requested.connect(self.remove_root_folder)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            expanded, vertical_scroll, horizontal_scroll = self.get_tree_state()
            self._tree_state_to_restore = {"expanded": expanded, "vertical_scroll": vertical_scroll, "horizontal_scroll": horizontal_scroll}
            self.mark_dirty()
            self.rescan()


    def remove_root_folder(self, settings):

        if settings not in self.folders:
            return

        expanded, vertical_scroll, horizontal_scroll = self.get_tree_state()
        result = QMessageBox.question(self, "Remove root folder", "Are you sure?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

        if result != QMessageBox.StandardButton.Yes:
            return

        self.folders.remove(settings)
        root_path = str(Path(settings.source_folder).resolve())
        expanded = {path for path in expanded if path != root_path and not path.startswith(root_path + os.sep)}
        self._tree_state_to_restore = {"expanded": expanded, "vertical_scroll": vertical_scroll, "horizontal_scroll": horizontal_scroll}
        self.mark_dirty()
        self.rescan()
        self.log_message(f"Removed from project: {settings.source_folder}")


    def get_tree_state(self):
        expanded = {path for path, item in self.items_by_path.items() if item.isExpanded()}
        return expanded, self.tree.verticalScrollBar().value(), self.tree.horizontalScrollBar().value()


    def restore_tree_state(self, expanded, vertical_scroll, horizontal_scroll=0):

        for path in sorted(expanded):
            item = self.items_by_path.get(path)

            if item is not None:
                item.setExpanded(True)

        QTimer.singleShot(0, lambda: self._restore_scroll_position(vertical_scroll, horizontal_scroll))


    def _restore_scroll_position(self, vertical_scroll, horizontal_scroll):
        self.tree.verticalScrollBar().setValue(max(0, int(vertical_scroll)))
        self.tree.horizontalScrollBar().setValue(max(0, int(horizontal_scroll)))


    def rescan(self, changed_paths=None):

        if self.rescan_worker and self.rescan_worker.isRunning():
            return

        if changed_paths is None:
            mode = "manual"
            changed_paths = set()
        else:
            mode = "incremental"
            changed_paths = { str(Path(p).resolve()) for p in changed_paths }

        expanded, vertical, horizontal = self.get_tree_state()

        self._tree_state_to_restore = {
            "expanded": expanded,
            "vertical_scroll": vertical,
            "horizontal_scroll": horizontal,
        }

        self._rescanning = True
        self.tree.setEnabled(False)
        self.tree.setCursor(Qt.CursorShape.WaitCursor)

        self.rescan_button.setEnabled(False)
        self.log_message("Rescan started...")

        self.rescan_worker = RescanWorker(self.folders, mode, changed_paths)
        self.rescan_worker.finished.connect(self._rescan_finished)
        self.rescan_worker.error.connect(self._rescan_error)
        self.rescan_worker.start()


    def _rescan_finished(self, mode, changed_paths, result):

        try:
            watched = result["watched"]
            snapshots = result["snapshots"]

            if mode == "manual":
                changed = {
                    path for path, old in self._directory_snapshot.items()
                    if snapshots.get(path) != old
                }

                if changed:
                    self._incremental_rescan(changed)
                else:
                    self.log_message("Rescan: no filesystem changes.")

            elif mode == "incremental":
                self._incremental_rescan(changed_paths)

            else:
                self._full_rescan()

            current = { str(Path(p).resolve()) for p in self._watcher.directories() }
            self._watcher.removePaths(list(current - watched))
            self._watcher.addPaths(list(watched - current))
            self._directory_snapshot = snapshots

            state = self._tree_state_to_restore

            if state:
                self.restore_tree_state(
                    state["expanded"],
                    state["vertical_scroll"],
                    state["horizontal_scroll"],
                )

            self.log_message("Rescan finished.")

        except Exception as exc:
            self._rescan_error(str(exc))

        finally:
            self._finish_rescan()


    def _rescan_error(self, message):
        self.log_message(f"Rescan ERROR: {message}")
        QMessageBox.critical(self, "Rescan error", message)
        self._finish_rescan()


    def _finish_rescan(self):
        self._rescanning = False
        self.tree.setCursor(Qt.CursorShape.ArrowCursor)
        self.tree.setEnabled(True)
        self.rescan_button.setEnabled(True)

        if self.rescan_worker:
            self.rescan_worker.deleteLater()
            self.rescan_worker = None


    def _manual_change_check(self):
        changed = set()

        for directory, old_signature in list(self._directory_snapshot.items()):
            new_signature = self._directory_signature(directory)

            if new_signature != old_signature:
                changed.add(directory)

                if new_signature is None:
                    self._directory_snapshot.pop(directory, None)
                else:
                    self._directory_snapshot[directory] = new_signature

        if changed:
            self._incremental_rescan(changed)
        else:
            self.log_message("Rescan: no filesystem changes.")


    def _full_rescan(self):
        self.items_by_path.clear()
        self.settings_by_item.clear()
        self._folder_status_cache.clear()
        self._file_status_cache.clear()
        self.tree.clear()

        for settings in self.folders:
            self.add_folder_item(settings)

        self.start_thumbnail_worker()


    def _incremental_rescan(self, changed_paths):
        affected = set()

        for changed in changed_paths:
            path = Path(changed).resolve()

            settings = self.find_settings_for_path(path)

            if settings is None:
                continue

            root = Path(settings.source_folder).resolve()

            # Если изменилась сама root.
            if path == root:
                affected.add(root)
                continue

            # Если directoryChanged пришёл для директории,
            # обновляем именно её.
            if path.is_dir():
                affected.add(path)
                continue

            # Если директория была удалена,
            # path.is_dir() == False.
            #
            # В таком случае обновляем существующего родителя.
            affected.add(path.parent)

        # Не нужно обновлять и parent, и его child отдельно.
        #
        # Например:
        #
        # root/a
        # root/a/b
        #
        # достаточно обновить root/a.
        normalized = set()

        for path in sorted(affected, key=lambda p: len(p.parts)):

            if any(
                path == parent
                or parent in path.parents
                for parent in normalized
            ):
                continue

            normalized.add(path)

        for folder in normalized:
            self._refresh_tree_folder(folder)

        # Новые директории должны попасть в watcher.
        self._register_watch_directories()


    def _refresh_tree_folder(self, folder):
        folder = Path(folder).resolve()
        settings = self.find_settings_for_path(folder)

        if settings is None:
            return

        item = self.items_by_path.get(str(folder))

        # Папка была удалена.
        if not folder.exists():

            if item is not None:
                parent = item.parent()

                self._remove_tree_item(item)

                if parent is not None:
                    self._invalidate_folder_cache(Path(parent.data(0, Qt.ItemDataRole.UserRole)))

            return

        if not folder.is_dir():
            return

        # Новая директория.
        if item is None:
            parent_folder = folder.parent
            parent_item = self.items_by_path.get(str(parent_folder))

            if parent_item is not None:
                self._refresh_tree_folder(parent_folder)

            return

        expanded = item.isExpanded()

        self._invalidate_file_status_cache(folder)
        self._invalidate_cache_for_tree(folder)

        # Удаляем только children этой папки.
        for index in range(item.childCount() - 1, -1, -1):
            child = item.child(index)

            self._remove_tree_item(child, detach=True)

        item.takeChildren()

        # Создаём только содержимое изменившейся папки.
        self._populate_folder_children(
            item,
            folder,
            settings,
        )

        item.setExpanded(expanded)

        # Thumbnail только для этой папки.
        self._start_folder_thumbnail_worker(folder)


    def _remove_tree_item(self, item, detach=False):

        for index in range(item.childCount() - 1, -1, -1):
            child = item.child(index)

            self._remove_tree_item(child, detach=True)

        path = item.data(0, Qt.ItemDataRole.UserRole)

        if path:
            path = str(Path(path).resolve())
            self.items_by_path.pop(path, None)

        self.settings_by_item.pop(id(item), None)

        if detach:
            parent = item.parent()

            if parent is not None:
                parent.removeChild(item)


    def start_conversion(self, settings, preset, folder):

        if self.thread:
            QMessageBox.warning(self, "Busy", "Another conversion is already running.")
            return

        if not preset.output_folder:
            QMessageBox.warning(self, "Output folder", f"Preset '{preset.name}' has no Output folder.")
            return

        local = self.get_local_preset(folder, preset) if settings.mode == "WebM" else None
        self.start_jobs([(settings, preset, Path(folder).resolve(), local)])


    def start_folder_all_conversions(self, settings, folder):

        if self.thread:
            QMessageBox.warning(self, "Busy", "Another conversion is already running.")
            return

        jobs = []

        for preset in settings.presets:

            if not preset.output_folder:
                continue

            local = self.get_local_preset(folder, preset) if settings.mode == "WebM" else None
            jobs.append((settings, preset, Path(folder).resolve(), local))

        if not jobs:
            QMessageBox.information(self, "Convert All", "This folder has no configured presets.")
            return

        self.start_jobs(jobs)


    def start_all_conversions(self):

        if self.thread:
            QMessageBox.warning(self, "Busy", "Another conversion is already running.")
            return

        jobs = []

        for settings in self.folders:

            for preset in settings.presets:

                if not preset.output_folder:
                    continue

                if settings.mode == "WebM":
                    folders = self.get_webm_folders(Path(settings.source_folder))
                else:
                    folders = [Path(settings.source_folder)]

                for folder in folders:
                    local = self.get_local_preset(folder, preset) if settings.mode == "WebM" else None
                    jobs.append((settings, preset, folder, local))

        if not jobs:
            QMessageBox.information(self, "Convert All", "No configured conversions found.")
            return

        self.start_jobs(jobs)


    def get_webm_folders(self, root):
        folders = []

        try:
            for folder in root.rglob("*"):

                if not folder.is_dir() or folder.name.lower() == "backup":
                    continue

                try:
                    if any(path.is_file() and path.suffix.lower() in WebMConverter.IMAGE_EXTENSIONS for path in folder.iterdir()):
                        folders.append(folder)

                except OSError:
                    continue

        except OSError:
            pass

        return folders


    def start_jobs(self, jobs):
        self.thread = ConversionThread(jobs)
        self.thread.message.connect(self.log_message)
        self.thread.error.connect(self.conversion_error)
        self.thread.progress.connect(self.update_progress)
        self.thread.finished_signal.connect(self.conversion_finished)
        self.convert_all_button.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Preparing...")
        self.thread.start()


    def format_eta(self, seconds):

        if seconds <= 0:
            return "0s"

        seconds = int(seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours:
            return f"{hours}h {minutes:02d}m"

        if minutes:
            return f"{minutes}m {seconds:02d}s"

        return f"{seconds}s"


    def update_progress(self, done, total, eta):
        total = max(1, int(total))
        done = min(max(0, int(done)), total)
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        percent = int(done * 100 / total) if total else 0

        if done >= total:
            self.progress.setFormat(f"{done}/{total} • 100% • Done")

        elif done == 0:
            self.progress.setFormat(f"0/{total} • 0% • Preparing...")

        else:
            self.progress.setFormat(f"{done}/{total} • {percent}% • ETA: {self.format_eta(eta)}")


    def stop_conversion(self):

        if not self.thread:
            return

        self.log_message("Stopping conversion...")
        self.stop_button.setEnabled(False)
        self.thread.stop()


    def conversion_error(self, message):
        self.log_message(f"ERROR: {message}")
        QMessageBox.critical(self, "Conversion error", message)


    def conversion_finished(self, changed_folders):

        if self.thread:
            self.thread.deleteLater()

        self.thread = None

        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setFormat("Done")

        self.convert_all_button.setEnabled(True)
        self.rescan_button.setEnabled(True)
        self.stop_button.setEnabled(False)

        if changed_folders:
            self.rescan(changed_folders)


    def get_project_data(self):
        expanded, vertical_scroll, horizontal_scroll = self.get_tree_state()
        return {
            "version": self.PROJECT_VERSION,
            "folders": [settings.to_dict() for settings in self.folders],
            "ui_state": {
                "expanded": sorted(expanded),
                "vertical_scroll": vertical_scroll,
                "horizontal_scroll": horizontal_scroll,
            },
        }


    def save_project(self):
        if self.project_filename:
            self.write_project(self.project_filename)
        else:
            self.save_project_as()


    def save_project_as(self):
        SAVES_DIR.mkdir(parents=True, exist_ok=True)
        filename, _ = QFileDialog.getSaveFileName(self, "Save Project", str(SAVES_DIR), "kConverter Project (*.kcp)")

        if not filename:
            return

        if not filename.lower().endswith(PROJECT_EXTENSION):
            filename += PROJECT_EXTENSION

        self.write_project(filename)


    def write_project(self, filename):

        try:
            path = Path(filename).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "wb") as file:
                pickle.dump(self.get_project_data(), file, protocol=pickle.HIGHEST_PROTOCOL)

            self.project_filename = str(path)
            self.write_last_project(path)
            self.mark_clean()
            self.log_message(f"Project saved: {path}")

        except Exception as exc:
            QMessageBox.critical(self, "Save error", str(exc))


    def load_project(self):

        if not self.confirm_save():
            return

        filename, _ = QFileDialog.getOpenFileName(self, "Load Project", str(SAVES_DIR), "kConverter Project (*.kcp)")

        if filename:
            self.read_project(filename)


    def read_project(self, filename):

        try:
            path = Path(filename).resolve()

            with open(path, "rb") as file:
                data = pickle.load(file)

            ui_state = data.get("ui_state", {})

            if not isinstance(ui_state, dict):
                ui_state = {}

            self._tree_state_to_restore = {
                "expanded": set(str(folder_path) for folder_path in ui_state.get("expanded", [])),
                "vertical_scroll": int(ui_state.get("vertical_scroll", 0)),
                "horizontal_scroll": int(ui_state.get("horizontal_scroll", 0)),
            }

            self.folders.clear()
            self.items_by_path.clear()
            self.settings_by_item.clear()
            self.tree.clear()

            for folder_data in data.get("folders", []):
                settings = FolderSettings.from_dict(folder_data)

                if settings.source_folder:
                    self.folders.append(settings)

            self.project_filename = str(path)
            self.write_last_project(path)
            self.mark_clean()
            self.rescan()
            self.log_message(f"Project loaded: {path}")

        except Exception as exc:
            QMessageBox.critical(self, "Load error", str(exc))


    def write_last_project(self, filename):

        try:
            SAVES_DIR.mkdir(parents=True, exist_ok=True)
            data = {"last_project": str(Path(filename).resolve())}

            with open(PERSISTENT_FILE, "wb") as file:
                pickle.dump(data, file, protocol=pickle.HIGHEST_PROTOCOL)

        except Exception:
            pass


    def load_last_project(self):
        filename = None

        if PERSISTENT_FILE.exists():
            try:
                with open(PERSISTENT_FILE, "rb") as file:
                    data = pickle.load(file)

                if isinstance(data, dict):
                    filename = data.get("last_project")

            except Exception:
                filename = None

        if filename:
            path = Path(filename)

            if path.exists() and path.is_file() and path.suffix.lower() == PROJECT_EXTENSION:
                self.read_project(str(path))
                return

        projects = list(SAVES_DIR.glob(f"*{PROJECT_EXTENSION}"))

        if projects:
            projects.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            self.read_project(str(projects[0]))


    def confirm_save(self):

        if not self.project_dirty:
            return True

        result = QMessageBox.question(self, "Unsaved changes", "The project has unsaved changes. Save them?", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)

        if result == QMessageBox.StandardButton.Save:
            self.save_project()
            return not self.project_dirty

        if result == QMessageBox.StandardButton.Discard:
            return True

        return False


    def log_message(self, message):
        self.log.append(str(message))


    def closeEvent(self, event):

        if self.thread:
            QMessageBox.warning(
                self,
                "Conversion running",
                "Stop the conversion before closing."
            )
            event.ignore()
            return

        if self.rescan_worker and self.rescan_worker.isRunning():
            self.rescan_worker.stop()

        if self.thumbnail_worker and self.thumbnail_worker.isRunning():
            self.thumbnail_worker.stop()

        if self.confirm_save():
            event.accept()
        else:
            event.ignore()


def main():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APPID)
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
