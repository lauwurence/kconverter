################################################################################
## Main Window

import os
import pickle

from pathlib import Path
from PyQt6.QtCore import Qt, QUrl, QSize, QTimer
from PyQt6.QtGui import QShortcut, QKeySequence, QIcon, QDesktopServices, QFont, QPixmap
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QTreeWidgetItem,
    QFileDialog, QTextEdit, QProgressBar, QDialog, QMessageBox,
    QLabel, QToolButton, QMenu, QComboBox
)

from core.conversion.worker import ConversionWorker
from core.conversion.image import ImageConverter
from core.conversion.webm import WebMConverter
from core.conversion.audio import AudioConversionWorker, SUPPORTED_BITRATES
from core.local_audio import LocalAudioDialog, AudioAwareSettingsDialog, read_local_audio_settings, write_local_audio_settings
from core.local_webm import LocalWebMDialog, normalize_webm_settings
from core.local_image import LocalImageDialog
from core.folder import FolderSettings
from core.thumbnail import ThumbnailWorker
from core.rescan import RescanWorker
from core.folder_tree import FolderTree
from core.preset import Preset
from core.utils import textutils, pathutils, setutils

from config import (
    VERSION, SAVES_DIR, CACHE_DIR, PROJECT_EXTENSION, ROOT_ROW_HEIGHT,
    FOLDER_ROW_HEIGHT, ICON,
)



def folder_cache_key(folder, preset):
    return str(Path(folder).resolve()), preset.cache_key


class MainWindow(QMainWindow):
    PROJECT_VERSION = 17



    def __init__(self):
        super().__init__()

        # Список объектов корневых папок
        self.folders = []

        # Путь проекта
        self.project_filename = None

        # Проект изменился?
        self.project_is_dirty = False

        self.items_by_path = {}
        self.settings_by_item = {}
        self._file_status_cache = {}

        self._conversion_changed_folders = set()
        self._conversion_queue = []
        self._active_conversion_job = None
        self._conversion_stop_requested = False

        self._folder_status_cache = {}
        self._directory_snapshot = {}

        # Threads & Workers
        self.conversion_worker = None
        self.thumbnail_worker = None
        self.rescan_worker = None

        # Ensure saves directory
        SAVES_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Build UI
        self.setWindowTitle("kConverter")
        self.setWindowIcon(QIcon(ICON))
        self.resize(1600, 800)
        self.setup_ui()
        self.setup_menu()
        self.setup_shortcuts()
        self.load_last_project()
        self.update_title()

        self.rebuild_folders()

        self.tree.itemExpanded.connect(self._on_item_expanded)


    def enqueue_conversion(self, job):
        """
        Add a conversion job to the queue.

        If nothing is currently running, start it immediately.
        Otherwise the job waits in the queue.
        """

        settings, preset, folder, local = job

        for queued in self._conversion_queue:
            q_settings, q_preset, q_folder, q_local = queued

            if (
                q_settings is settings
                and q_preset is preset
                and Path(q_folder).resolve() == Path(folder).resolve()
            ):
                return

        if self._active_conversion_job is not None:
            if (
                self._active_conversion_job[0] is settings
                and self._active_conversion_job[1] is preset
                and Path(self._active_conversion_job[2]).resolve()
                    == Path(folder).resolve()
            ):
                return

        # Новая очередь/новая задача снова разрешает конвертацию.
        self._conversion_stop_requested = False

        self._conversion_queue.append(job)
        self.update_conversion_button_states()

        if self.conversion_worker is None:
            self.start_next_conversion()


    def start_next_conversion(self):
        """
        Start the next conversion from the queue.
        """

        if self._conversion_stop_requested:
            self._active_conversion_job = None
            self._conversion_queue.clear()
            self.update_conversion_button_states()
            return

        if self.conversion_worker is not None:
            return

        if not self._conversion_queue:
            self._active_conversion_job = None
            self.update_conversion_button_states()
            return

        job = self._conversion_queue.pop(0)
        self._active_conversion_job = job

        self.update_conversion_button_states()
        self.start_covnersion_jobs([job])


    def _image_folder_signature(self, folder):
        folder = Path(folder).resolve()
        entries = []

        try:
            for source in pathutils.iter_files(folder, suffix=".png"):

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

        return tuple(sorted(entries))


    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Top
        top = QHBoxLayout()
        layout.addLayout(top)

        # Rescan
        self.rescan_button = QPushButton("")
        self.rescan_button.setIcon(QIcon("icons/rescan.svg"))
        self.rescan_button.setToolTip("Rescan all folders.")
        self.rescan_button.clicked.connect(lambda: self.rescan())
        top.addWidget(self.rescan_button)

        # Move folder up
        self.move_folder_up_button = QPushButton()
        self.move_folder_up_button.setIcon(QIcon("icons/arrow_up.svg"))
        self.move_folder_up_button.setToolTip("Move selected root folder up")
        self.move_folder_up_button.setFixedSize(28, 28)
        self.move_folder_up_button.clicked.connect(self.move_selected_folder_up)
        top.addWidget(self.move_folder_up_button)

        # Move folder down
        self.move_folder_down_button = QPushButton()
        self.move_folder_down_button.setIcon(QIcon("icons/arrow_down.svg"))
        self.move_folder_down_button.setToolTip("Move selected root folder down")
        self.move_folder_down_button.setFixedSize(28, 28)
        self.move_folder_down_button.clicked.connect(self.move_selected_folder_down)
        top.addWidget(self.move_folder_down_button)

        top.addStretch()

        # Folders
        self.tree = FolderTree()
        self.tree.folders_dropped.connect(self.add_folders)
        self.tree.currentItemChanged.connect(lambda current, previous: self.update_folder_move_buttons())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_folder_context_menu)

        layout.addWidget(self.tree)

        # Log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(100)
        layout.addWidget(self.log)

        # Bottom
        bottom = QHBoxLayout()
        layout.addLayout(bottom)

        # Stop
        self.stop_button = QPushButton("STOP")
        self.stop_button.setMinimumWidth(90)
        self.stop_button.setMaximumWidth(105)
        self.stop_button.setMinimumHeight(36)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_conversion)
        bottom.addWidget(self.stop_button)

        # Progress Bar
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
        bottom.addWidget(self.progress, 1)

        # Convert All
        self.convert_all_button = QPushButton("Convert All")
        self.convert_all_button.setMinimumWidth(120)
        self.convert_all_button.setMinimumHeight(36)
        self.convert_all_button.clicked.connect(self.start_all_conversions)
        bottom.addWidget(self.convert_all_button)

        self.update_folder_move_buttons()


    def move_selected_folder_up(self):
        item = self.tree.currentItem()

        if item is None:
            return

        # Только корневые папки.
        if item.parent() is not None:
            return

        index = self.tree.indexOfTopLevelItem(item)

        if index <= 0:
            return

        settings = self.settings_by_item.get(id(item))

        if settings is None:
            return

        # Меняем порядок в модели.
        self.folders[index - 1], self.folders[index] = (
            self.folders[index],
            self.folders[index - 1],
        )

        self.mark_dirty()

        # Пересоздаём дерево, чтобы корректно восстановить
        # все item widgets.
        self.rebuild_folders()

        # Снова выбираем перемещённую папку.
        new_item = self.items_by_path.get(
            str(Path(settings.source_folder).resolve())
        )

        if new_item is not None:
            self.tree.setCurrentItem(new_item)

        self.update_folder_move_buttons()


    def update_folder_move_buttons(self):
        item = self.tree.currentItem()

        if item is None or item.parent() is not None:
            self.move_folder_up_button.setEnabled(False)
            self.move_folder_down_button.setEnabled(False)
            return

        index = self.tree.indexOfTopLevelItem(item)
        count = self.tree.topLevelItemCount()

        self.move_folder_up_button.setEnabled(index > 0)
        self.move_folder_down_button.setEnabled(index >= 0 and index < count - 1)


    def move_selected_folder_down(self):
        item = self.tree.currentItem()

        if item is None:
            return

        # Только корневые папки.
        if item.parent() is not None:
            return

        index = self.tree.indexOfTopLevelItem(item)
        count = self.tree.topLevelItemCount()

        if index < 0 or index >= count - 1:
            return

        settings = self.settings_by_item.get(id(item))

        if settings is None:
            return

        # Меняем порядок в модели.
        self.folders[index + 1], self.folders[index] = (
            self.folders[index],
            self.folders[index + 1],
        )

        self.mark_dirty()

        # Пересоздаём дерево, чтобы корректно восстановить
        # все item widgets.
        self.rebuild_folders()

        # Снова выбираем перемещённую папку.
        new_item = self.items_by_path.get(
            str(Path(settings.source_folder).resolve())
        )

        if new_item is not None:
            self.tree.setCurrentItem(new_item)

        self.update_folder_move_buttons()


    def setup_menu(self):
        file_menu = self.menuBar().addMenu("File")      # type: ignore
        save = file_menu.addAction("Save Project")      # type: ignore
        save_as = file_menu.addAction("Save Project As...")     # type: ignore
        file_menu.addSeparator()        # type: ignore
        load = file_menu.addAction("Load Project...")       # type: ignore
        file_menu.addSeparator()        # type: ignore
        exit_action = file_menu.addAction("Exit")       # type: ignore
        save.triggered.connect(self.save_project)       # type: ignore
        save_as.triggered.connect(self.save_project_as)     # type: ignore
        load.triggered.connect(self.load_project)       # type: ignore
        exit_action.triggered.connect(self.close)       # type: ignore


    def setup_shortcuts(self):
        save = QShortcut(QKeySequence("Ctrl+S"), self)
        save.activated.connect(self.save_project)
        save_as = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        save_as.activated.connect(self.save_project_as)


    def update_title(self):
        name = f"{Path(self.project_filename).stem} [{self.project_filename}]" if self.project_filename else "Untitled"

        if self.project_is_dirty:
            name = "* " + name

        self.setWindowTitle(f'{name} - kConverter {".".join([str(v) for v in VERSION])}')
        self.setWindowIcon(QIcon(ICON))


    def mark_dirty(self):
        self.project_is_dirty = True
        self.update_title()


    def mark_clean(self):
        self.project_is_dirty = False
        self.update_title()


    def add_folders(self, folders):
        existing = set([ settings.source_folder for settings in self.folders ])
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
        self._add_folder_placeholder(item)


    def _add_folder_placeholder(self, item):
        """Add a dummy child so the folder gets an expand arrow."""
        placeholder = QTreeWidgetItem()
        placeholder.setData(0, Qt.ItemDataRole.UserRole, None)
        item.addChild(placeholder)

    def _on_item_expanded(self, item):
        """Scan and populate only the folder that was just expanded."""
        path = item.data(0, Qt.ItemDataRole.UserRole)

        if not path:
            return

        folder = Path(path).resolve()

        if not folder.is_dir():
            return

        settings = self.settings_by_item.get(id(item))
        if settings is None:
            settings = self.find_settings_for_path(folder)

        if settings is None:
            return

        # Remove placeholder / old children only on first load.
        if item.childCount() == 1:
            child = item.child(0)
            if child.data(0, Qt.ItemDataRole.UserRole) is None:
                item.takeChildren()

        # Already populated.
        FOLDER_LOADED_ROLE = Qt.ItemDataRole.UserRole + 1

        if item.data(0, FOLDER_LOADED_ROLE):
            return

        self._populate_folder_children(item, folder, settings)

        item.setData(0, FOLDER_LOADED_ROLE, True)

        # Create thumbnails only for opened non-root folders.
        if item.parent() is not None:
            self.start_thumbnail_worker(folder)


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
            row.addWidget(self.create_root_controls(settings))

            mode = QLabel(f"[{settings.mode}]")
            mode_font = QFont()
            mode_font.setPointSize(10)
            mode_font.setBold(True)
            mode.setFont(mode_font)
            row.addWidget(mode)

        row.addStretch()
        return widget


    def _populate_folder_children(self, parent, folder, settings):
        folder = Path(folder).resolve()

        try:
            entries = sorted(
                (
                    path
                    for path in folder.iterdir()
                    if not path.name.startswith(".")
                ),
                key=lambda path: (
                    not path.is_dir(),
                    path.name.lower(),
                ),
            )

        except (PermissionError, OSError):
            return

        for entry in entries:
            child = QTreeWidgetItem([entry.name])

            child.setData(
                0,
                Qt.ItemDataRole.UserRole,
                str(entry),
            )

            child.setToolTip(
                0,
                str(entry),
            )

            child.setSizeHint(
                0,
                QSize(0, FOLDER_ROW_HEIGHT),
            )

            parent.addChild(child)

            resolved = str(entry.resolve())

            self.items_by_path[resolved] = child

            # -------------------------------------------------------------
            # Folder
            # -------------------------------------------------------------

            if entry.is_dir():
                widget = self.create_folder_status(
                    settings,
                    entry,
                    False,
                )

                self.tree.setItemWidget(
                    child,
                    1,
                    widget,
                )

                self._add_folder_placeholder(child)

            # -------------------------------------------------------------
            # File
            # -------------------------------------------------------------

            elif entry.is_file():

                # Images mode
                if settings.mode == "Images":
                    if entry.suffix.lower() == ".png":
                        widget = self.create_file_status(
                            settings,
                            entry,
                        )

                        self.tree.setItemWidget(
                            child,
                            1,
                            widget,
                        )

                # Audio mode
                elif settings.mode == "Audio":
                    widget = self.create_audio_file_status(
                        settings,
                        entry,
                    )

                    self.tree.setItemWidget(
                        child,
                        1,
                        widget,
                    )

    def create_audio_file_status(self, settings, source):
        """
        Create status buttons for an Audio source file.

        Each preset gets one button showing the converted file size.
        """
        widget = QWidget()

        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4 + 138, 0, 4, 0)
        layout.setSpacing(160)

        source = Path(source).resolve()

        for preset in settings.presets:
            output = self.get_audio_output_file(
                settings,
                preset,
                source,
            )

            if output is not None and output.is_file():
                try:
                    size = output.stat().st_size
                except OSError:
                    size = 0

                button = QPushButton(
                    textutils.format_size(size)
                )

                button.setFixedHeight(25)
                button.setFixedWidth(75)
                button.setFlat(True)

                button.setToolTip(
                    f"Open converted audio:\n"
                    f"{output}\n"
                    f"Size: {textutils.format_size(size)}"
                )

                button.clicked.connect(
                    lambda checked=False, path=output:
                        self.open_file(path)
                )

                button.setContextMenuPolicy(
                    Qt.ContextMenuPolicy.CustomContextMenu
                )

                button.customContextMenuRequested.connect(
                    lambda pos,
                        button=button,
                        path=output,
                        source=source:
                        self.show_output_context_menu(
                            button,
                            path,
                            refresh_callback=lambda s=source:
                                self._refresh_file_status_widget(s),
                        )
                )

            else:
                button = QPushButton("-")

                button.setFixedHeight(25)
                button.setFixedWidth(75)
                button.setFlat(True)
                button.setEnabled(False)

                button.setStyleSheet(
                    "QPushButton:disabled { color: #888; }"
                )

                button.setToolTip(
                    "Audio file has not been converted yet"
                )

            layout.addWidget(button)

        layout.addStretch()

        return widget

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

                if not root:
                    local_button = QToolButton()

                    has_local = self.get_local_preset(folder, preset) is not None
                    local_button.setIcon(QIcon("icons/settings_local.svg" if has_local else "icons/settings.svg"))
                    local_button.setToolTip("Local WebM settings" + (" (override active)" if has_local else ""))
                    local_button.setFixedSize(27, 25)

                    local_button.clicked.connect(
                        lambda checked=False,
                            s=settings,
                            p=preset,
                            f=Path(folder):
                            self.edit_local_webm(s, p, f)
                    )

                    layout.addWidget(local_button)

            elif settings.mode == "Images":

                if not root:
                    local_button = QToolButton()

                    has_local = self.has_local_image_settings(folder, preset)

                    local_button.setIcon(QIcon("icons/settings_local.svg" if has_local else "icons/settings.svg"))
                    local_button.setToolTip(
                        "Local Image settings"
                        + (" (override active)" if has_local else "")
                    )

                    local_button.setFixedSize(27, 25)

                    local_button.clicked.connect(
                        lambda checked=False,
                            s=settings,
                            p=preset,
                            f=Path(folder):
                            self.edit_local_image(s, p, f)
                    )

                    layout.addWidget(local_button)

            elif settings.mode == "Audio":

                if not root:
                    has_local = self.has_local_audio_settings(folder, preset)
                    local_button = QToolButton()
                    local_button.setIcon(QIcon("icons/settings_local.svg" if has_local else "icons/settings.svg"))
                    local_button.setToolTip(
                        "Local Audio settings"
                        + (" (override active)" if has_local else "")
                    )
                    local_button.setFixedSize(27, 25)
                    local_button.clicked.connect(
                        lambda checked=False,
                            s=settings,
                            p=preset,
                            f=Path(folder):
                            self.edit_local_audio(s, p, f)
                    )
                    layout.addWidget(local_button)

            button = QPushButton(preset.name)

            if preset.panorama:
                button.setIcon(QIcon("icons/panorama.svg"))

            button.setProperty("conversion_button", True)
            button.setProperty("conversion_original_text", preset.name)
            button.setProperty("conversion_preset", preset.cache_key)
            button.setProperty("conversion_folder", str(Path(folder).resolve()))
            button.setProperty("conversion_configured", bool(preset.output_folder.strip()))
            button.setToolTip(f'Convert this folder using "{preset.name}"')
            button.setFixedHeight(25)
            button.setFixedWidth(75)

            if root:
                button.setFixedWidth(75 + 31)

                if settings.mode == "Images" or settings.mode == "Audio":
                    button.setFixedWidth(75 + 30 + 30)

                button.setFixedHeight(30)

            if settings.mode == "Images":
                outdated = self.folder_has_outdated_images(settings, preset, folder)

            if settings.mode == "Images" and preset.output_folder:

                if outdated:
                    button.setStyleSheet("""QPushButton { color: #ff9800; }""")
                    button.setToolTip(
                        f'Folder contains outdated images.\n'
                        f'Convert this folder using "{preset.name}"'
                        )

            if not preset.output_folder.strip():
                button.setEnabled(False)
                button.setStyleSheet("QPushButton:disabled { color: #888; }")
                button.setToolTip(f'Preset "{preset.name}" has no Output folder.')
            else:
                button.clicked.connect(lambda checked=False, s=settings, p=preset, f=Path(folder): self.start_conversion(s, p, f))

            layout.addWidget(button)

            if settings.mode == "Images":

                if not root:
                    open_button = QToolButton()
                    open_button.setIcon(QIcon("icons/folder.svg"))
                    open_button.setToolTip("Open converted images folder")
                    open_button.setFixedWidth(25)
                    open_button.setFixedHeight(25)

                    open_button.clicked.connect(lambda checked=False, s=settings, p=preset, f=Path(folder): self.open_conversion_result(s, p, f))
                    open_button.setEnabled(bool(preset.output_folder))
                    layout.addWidget(open_button)

                folder_size = self.get_folder_output_size(settings, preset, folder)

                size_label = QLabel(textutils.format_size(folder_size))
                size_label.setFixedWidth(75)
                size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

                if outdated:
                    size_label.setStyleSheet("QLabel { color: #ff9800; }")
                else:
                    size_label.setStyleSheet("QLabel { color: #888; }")

                layout.addWidget(size_label)

            elif settings.mode == "Audio":

                if not root:
                    audio_button = QToolButton()
                    audio_button.setIcon(QIcon("icons/folder.svg"))
                    audio_button.setToolTip("Open converted audio folder")
                    audio_button.setFixedWidth(25)
                    audio_button.setFixedHeight(25)
                    audio_button.clicked.connect(lambda checked=False, s=settings, p=preset, f=Path(folder): self.open_conversion_result(s, p, f))
                    layout.addWidget(audio_button)

                folder_size = self.get_audio_folder_output_size(
                    settings,
                    preset,
                    folder,
                )

                size_label = QLabel(textutils.format_size(folder_size))
                size_label.setFixedWidth(75)
                size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

                layout.addWidget(size_label)

            else:
                local = self.get_local_preset(folder, preset)
                webm_output = None

                if preset.output_folder.strip():
                    webm_output = WebMConverter(folder, preset, local, source_root=settings.source_folder).get_output_file()

                if webm_output is not None and webm_output.is_file():
                    webm_size = webm_output.stat().st_size

                    webm_button = QPushButton(textutils.format_size(webm_size))
                    if not webm_size:
                        webm_button.setIcon(QIcon("icons/webm_play_error.svg"))
                    else:
                        webm_button.setIcon(QIcon("icons/webm_play.svg"))

                    webm_button.setFixedHeight(25)
                    webm_button.setFixedWidth(75)
                    webm_button.setToolTip(f"Open WebM animation\n{webm_output}")

                    webm_button.clicked.connect(
                        lambda checked=False, s=settings, p=preset, f=Path(folder):
                            self.open_conversion_result(s, p, f)
                    )

                    webm_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

                    webm_button.customContextMenuRequested.connect(
                        lambda pos,
                            button=webm_button,
                            path=webm_output,
                            folder=Path(folder):
                            self.show_output_context_menu(
                                button,
                                path,
                                refresh_callback=lambda f=folder:
                                    self._refresh_folder_status_widgets({f}),
                            )
                    )

                else:
                    webm_button = QPushButton(textutils.format_size(0))
                    webm_button.setFixedHeight(25 if not root else 30)
                    webm_button.setFixedWidth(75)
                    webm_button.setEnabled(False)
                    webm_button.setFlat(True)
                    webm_button.setStyleSheet("QPushButton:disabled { color: #888; }")
                    webm_button.setToolTip("WebM file does not exist yet")

                layout.addWidget(webm_button)

        all_button = QPushButton("All")
        all_button.setToolTip("Convert this folder using all presets")
        all_button.setFixedHeight(25 if not root else 30)
        all_button.setFixedWidth(40)
        all_button.clicked.connect(lambda checked=False, s=settings, f=Path(folder): self.start_folder_all_conversions(s, f))
        layout.addWidget(self.create_separator())
        layout.addWidget(all_button)
        layout.addStretch()

        return widget

    # =========================================================================
    # Audio output helpers
    # =========================================================================

    def show_output_folder_context_menu(
        self,
        button,
        path,
        refresh_callback=None,
    ):
        """
        Context menu for an Audio output folder.
        """
        path = Path(path).resolve()

        if not path.exists() or not path.is_dir():
            return

        menu = QMenu(button)

        open_action = menu.addAction(
            "Open Folder"
        )

        action = menu.exec(
            button.mapToGlobal(
                button.rect().bottomLeft()
            )
        )

        if action == open_action:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(
                    str(path)
                )
            )

            if refresh_callback:
                refresh_callback()

    def get_audio_output_folder(self, settings, preset, folder):
        """
        Return the output folder corresponding to the source folder.

        Example:

            Source:
                /Music/Album

            Project source:
                /Music

            Output:
                /Converted

            Result:
                /Converted/Album
        """
        if not preset or not preset.output_folder:
            return Path()

        folder = Path(folder).resolve()
        source_root = Path(settings.source_folder).resolve()
        output_root = Path(preset.output_folder).resolve()

        try:
            relative = folder.relative_to(source_root)
        except ValueError:
            # The folder is outside the project source root.
            relative = Path(folder.name)

        return output_root / relative


    def get_audio_output_file(self, settings, preset, source):
        """
        Find the converted audio file corresponding to one source file.

        The audio converter may change the extension, therefore we first
        check an exact filename and then search by filename stem.
        """
        if not preset or not preset.output_folder:
            return None

        source = Path(source).resolve()

        output_folder = self.get_audio_output_folder(
            settings,
            preset,
            source.parent,
        )

        if not output_folder.exists() or not output_folder.is_dir():
            return None

        # First try an exact filename.
        exact = output_folder / source.name

        if exact.is_file():
            return exact

        # Audio conversion normally changes the extension
        # (for example WAV -> MP3), so search by stem.
        try:
            candidates = [
                path
                for path in output_folder.iterdir()
                if path.is_file()
                and path.stem.lower() == source.stem.lower()
            ]
        except OSError:
            return None

        if not candidates:
            return None

        candidates.sort(key=lambda path: path.name.lower())

        return candidates[0]


    def get_audio_output_size(self, settings, preset, folder):
        """
        Return the total size of converted audio files in a folder.

        Only files directly inside the corresponding output folder
        are counted.
        """
        if not preset or not preset.output_folder:
            return 0

        output_folder = self.get_audio_output_folder(
            settings,
            preset,
            folder,
        )

        if not output_folder.exists() or not output_folder.is_dir():
            return 0

        total = 0

        try:
            for path in output_folder.iterdir():
                if not path.is_file():
                    continue

                try:
                    total += path.stat().st_size
                except OSError:
                    continue

        except OSError:
            return 0

        return total


    def get_audio_file_size(self, settings, preset, source):
        """
        Return the size of one converted audio file.

        Returns 0 when the converted file does not exist.
        """
        output = self.get_audio_output_file(
            settings,
            preset,
            source,
        )

        if output is None:
            return 0

        try:
            return output.stat().st_size
        except OSError:
            return 0


    def _audio_folder_signature(self, settings, preset, folder):
        """
        Signature of the Audio output folder.

        Used for cache invalidation.
        """
        output_folder = self.get_audio_output_folder(
            settings,
            preset,
            folder,
        )

        if not output_folder.exists() or not output_folder.is_dir():
            return None

        entries = []

        try:
            for path in output_folder.iterdir():
                if not path.is_file():
                    continue

                try:
                    stat = path.stat()

                    entries.append(
                        (
                            path.name,
                            stat.st_mtime_ns,
                            stat.st_size,
                        )
                    )

                except OSError:
                    continue

        except OSError:
            return None

        return tuple(sorted(entries))


    def _audio_source_signature(self, source):
        """
        Signature of a source audio file.
        """
        source = Path(source).resolve()

        try:
            stat = source.stat()
        except OSError:
            return None

        return (
            stat.st_mtime_ns,
            stat.st_size,
        )


    def _invalidate_audio_status_cache(self, folder):
        folder = Path(folder).resolve()

        for key in list(self._folder_status_cache):
            if len(key) < 4:
                continue

            if key[0] != str(folder):
                continue

            if key[-1] == "audio":
                del self._folder_status_cache[key]

    def show_folder_context_menu(self, pos):

        item = self.tree.itemAt(pos)

        if item is None:
            return

        path = item.data(0, Qt.ItemDataRole.UserRole)

        if not path:
            return

        path = Path(path).resolve()

        if not path.is_dir():
            return

        menu = QMenu(self.tree)

        open_folder_action = menu.addAction("Open Folder")

        action = menu.exec(self.tree.viewport().mapToGlobal(pos))

        if action == open_folder_action:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


    def show_output_context_menu(self, button, path, refresh_callback=None):

        path = Path(path).resolve()

        if not path.exists():
            return

        menu = QMenu(button)

        open_action = menu.addAction("Open File")
        open_folder_action = menu.addAction("Open Folder")

        menu.addSeparator()

        delete_action = menu.addAction("Delete File")

        action = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

        if action == open_action:
            self.open_file(path)

        elif action == open_folder_action:
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

        elif action == delete_action:

            if not path.is_file():
                return

            try:
                path.unlink(missing_ok=True)

                if path.suffix.lower() == ".webm":
                    path.with_suffix(".jpg").unlink(missing_ok=True)
                    path.with_suffix(".webp").unlink(missing_ok=True)

            except OSError as exc:
                QMessageBox.critical(self, "Delete error", str(exc))
                return

            # Файл удалён. Теперь обновляем именно тот
            # элемент интерфейса, которому он принадлежит.
            if refresh_callback:
                refresh_callback()


    def _refresh_file_status_widget(self, source):
        source = Path(source).resolve()

        item = self.items_by_path.get(
            str(source)
        )

        if item is None:
            return

        settings = self.find_settings_for_path(
            source
        )

        if settings is None:
            return

        # Invalidate file cache.
        self._invalidate_file_status_cache(
            source.parent
        )

        # Rebuild appropriate widget.
        if settings.mode == "Audio":
            widget = self.create_audio_file_status(
                settings,
                source,
            )
        else:
            widget = self.create_file_status(
                settings,
                source,
            )

        self.tree.removeItemWidget(
            item,
            1,
        )

        self.tree.setItemWidget(
            item,
            1,
            widget,
        )

        # Folder size also changed.
        self._refresh_folder_status_widgets(
            {source.parent}
        )

    def update_conversion_button_states(self):
        """
        Synchronize all conversion buttons with the current queue.
        """

        active = self._active_conversion_job
        queued = self._conversion_queue

        for folder_path, item in self.items_by_path.items():
            folder = Path(folder_path)

            widget = self.tree.itemWidget(item, 1)

            if widget is None:
                continue

            for button in widget.findChildren(QPushButton):

                if not button.property("conversion_button"):
                    continue

                preset_key = button.property("conversion_preset")

                is_active = (
                    active is not None
                    and active[1].cache_key == preset_key
                    and Path(active[2]).resolve() == folder
                )

                is_queued = any(
                    job[1].cache_key == preset_key
                    and Path(job[2]).resolve() == folder
                    for job in queued
                )

                preset = None

                for f in self.folders:

                    for p in f.presets:

                        if p.cache_key == preset_key:
                            preset = p
                            break

                if is_active or is_queued:
                    button.setText("")
                    button.setIcon(QIcon("icons/status_loading.svg"))
                    button.setEnabled(False)

                else:
                    button.setText(button.property("conversion_original_text") or button.text())

                    if preset and preset.panorama:
                        button.setIcon(QIcon("icons/panorama.svg"))
                    else:
                        button.setIcon(QIcon())

                    button.setEnabled(bool(button.property("conversion_configured")))


    def create_file_status(self, settings, source):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4 + 138, 0, 4, 0)
        layout.setSpacing(160)

        for preset in settings.presets:
            output, status, outdated = self.get_file_status(source, settings, preset)

            button = QPushButton(status)
            button.setFixedHeight(25)
            button.setFixedWidth(75)
            button.setFlat(True)

            if output is not None and output.exists():
                button.setIcon(QIcon("icons/open_image.svg"))
                button.setToolTip(f"Open converted image: {preset.name}\n{output}")

                if outdated:
                    button.setStyleSheet("QPushButton { color: #ff9800; }")
                    button.setToolTip(
                        f"Image is outdated and needs reconversion: "
                        f"{preset.name}\n{output}"
                    )

                button.clicked.connect(lambda checked=False, path=output: self.open_file(path))

                button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

                button.customContextMenuRequested.connect(
                    lambda pos,
                        button=button,
                        path=output,
                        source=Path(source):
                        self.show_output_context_menu(
                            button,
                            path,
                            refresh_callback=lambda s=source:
                                self._refresh_file_status_widget(s),
                        )
                )

            else:

                if not output:
                    button.setText("")
                    # button.setIcon(QIcon("icons/not_configured.svg"))

                button.setEnabled(False)
                button.setStyleSheet("QPushButton:disabled { color: #888; }")

            layout.addWidget(button)

        layout.addStretch()
        return widget


    def folder_has_outdated_images(self, settings, preset, folder):

        if settings.mode != "Images":
            return False

        folder = Path(folder).resolve()

        effective_preset = self.get_local_image_preset(folder, preset)

        if not effective_preset.output_folder.strip():
            return False

        signature = self._image_folder_signature(folder)

        if signature is None:
            return False

        key = folder_cache_key(folder, effective_preset)

        cached = self._folder_status_cache.get(key)

        if cached is not None and (cached.get("signature") == signature) and ("outdated" in cached):
            return cached["outdated"]

        converter = ImageConverter(
            settings.source_folder,
            effective_preset,
            source_root=settings.source_folder,
        )

        converter.cache = converter.read_cache()

        outdated = False

        for source in pathutils.iter_files(folder, suffix=".png"):

            try:
                output = converter.get_output_file(source)

                # Нет результата
                if not output.exists():
                    outdated = True
                    break

                source_stat = source.stat()

                relative_file = source.relative_to(converter.source_root).as_posix()

                expected_cache = (
                    int(source_stat.st_mtime),
                    effective_preset.cache_key,
                )

                actual_cache = converter.cache.get(relative_file)

                # Настройки пресета изменились
                if actual_cache != expected_cache:
                    outdated = True
                    break

                # Дополнительная проверка времени
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

    def get_audio_output_folder(self, settings, preset, folder):
        """
        Возвращает папку, в которую Audio-конвертер складывает
        результат для указанной source-папки.

        Для root:
            source_root/
            output_root/

        Для подпапки:
            source_root/music/
            output_root/music/
        """
        if not preset.output_folder:
            return None

        source_root = Path(settings.source_folder).resolve()
        folder = Path(folder).resolve()
        output_root = Path(preset.output_folder).resolve()

        try:
            relative = folder.relative_to(source_root)
        except ValueError:
            return None

        return output_root / relative


    def _audio_folder_signature(self, settings, preset, folder):
        """
        Сигнатура output-папки для кэширования размера.
        Меняется, когда появляются/изменяются/удаляются
        сконвертированные файлы.
        """
        output_folder = self.get_audio_output_folder(
            settings,
            preset,
            folder,
        )

        if output_folder is None or not output_folder.exists():
            return ()

        entries = []

        try:
            for path in output_folder.rglob("*"):
                if not path.is_file():
                    continue

                try:
                    stat = path.stat()
                except OSError:
                    continue

                entries.append((
                    path.relative_to(output_folder).as_posix(),
                    stat.st_mtime_ns,
                    stat.st_size,
                ))

        except OSError:
            return None

        return tuple(sorted(entries))


    def get_audio_folder_output_size(self, settings, preset, folder):
        """
        Суммарный размер всех сконвертированных файлов
        в output-папке Audio.
        """
        if not preset.output_folder:
            return 0

        folder = Path(folder).resolve()

        effective_settings = self.get_local_audio_settings(
            folder,
            preset,
        )

        # Включаем bitrate в ключ кэша, чтобы после изменения
        # локальных настроек размер пересчитался.
        key = (
            str(folder),
            preset.cache_key,
            effective_settings["bitrate"],
            "audio",
        )

        signature = self._audio_folder_signature(
            settings,
            preset,
            folder,
        )

        if signature is None:
            return 0

        cached = self._folder_status_cache.get(key)

        if (
            cached is not None
            and cached.get("signature") == signature
            and "output_size" in cached
        ):
            return cached["output_size"]

        output_folder = self.get_audio_output_folder(
            settings,
            preset,
            folder,
        )

        total = 0

        if output_folder is not None and output_folder.exists():
            try:
                for path in output_folder.rglob("*"):
                    if not path.is_file():
                        continue

                    try:
                        total += path.stat().st_size
                    except OSError:
                        continue

            except OSError:
                pass

        cached = self._folder_status_cache.setdefault(key, {})
        cached["signature"] = signature
        cached["output_size"] = total

        return total

    def get_folder_output_size(self, settings, preset, folder):

        if not preset.output_folder.strip():
            return 0

        folder = Path(folder).resolve()

        effective_preset = self.get_local_image_preset(folder, preset)
        key = folder_cache_key(folder, effective_preset)

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
            effective_preset,
            source_root=settings.source_folder,
        )

        total = 0

        try:
            for source in pathutils.iter_files(folder, suffix=".png"):

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
            cached_folder = Path(key[0])

            try:
                cached_folder.relative_to(folder)
            except ValueError:
                continue

            del self._folder_status_cache[key]


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
        source = Path(source).resolve()

        settings = self.find_settings_for_path(source)

        if settings is None or not preset.output_folder:
            return None

        effective_preset = self.get_local_image_preset(
            source.parent,
            preset,
        )

        return ImageConverter(
            settings.source_folder,
            effective_preset,
            source_root=settings.source_folder,
        ).get_output_file(source)


    def get_file_status(self, source, settings, preset):
        source = Path(source).resolve()

        if settings is None:
            return None, "", False

        effective_preset = self.get_local_image_preset(source.parent, preset)

        if not effective_preset.output_folder.strip():
            return None, "", False

        converter = ImageConverter(
            settings.source_folder,
            effective_preset,
            source_root=settings.source_folder,
        )

        output = converter.get_output_file(source)

        try:
            source_stat = source.stat()

            if output.exists():
                output_stat = output.stat()
            else:
                output_stat = None

        except OSError:
            return output, "", False

        key = (str(source), effective_preset.cache_key)

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
            return (
                output,
                cached["size"],
                cached["outdated"],
            )

        if output_stat is None:
            size = "-"
            outdated = True

        else:
            converter.cache = converter.read_cache()
            relative_file = source.relative_to(converter.source_root).as_posix()
            expected_cache = (int(source_stat.st_mtime), effective_preset.cache_key,)
            actual_cache = converter.cache.get(relative_file)

            outdated = actual_cache != expected_cache or output_stat.st_mtime_ns < source_stat.st_mtime_ns

            size = textutils.format_size(output_stat.st_size)

        self._file_status_cache[key] = {
            "signature": signature,
            "size": size,
            "outdated": outdated,
        }

        return output, size, outdated


    def _invalidate_file_status_cache(self, folder):
        folder = Path(folder).resolve()

        for key in list(self._file_status_cache):
            source = Path(key[0])

            try:
                source.relative_to(folder)
                del self._file_status_cache[key]

            except ValueError:
                pass


    def get_local_image_preset(self, folder, preset):

        if not preset:
            return None

        folder = Path(folder).resolve()
        data = setutils.read_local_image_settings(folder)

        current = data.get(preset.name)

        if not isinstance(current, dict):
            return preset

        try:
            local_preset = Preset.from_dict(preset.to_dict())
            enabled = set(current.get("enabled_overrides", getattr(preset, "enabled_overrides", set())))

            for key, value in current.items():

                if key in ["name", "enabled_overrides"]:
                    continue

                if key not in enabled:
                    continue

                if hasattr(local_preset, key):
                    setattr(local_preset, key, value)

            local_preset.name = preset.name

            return local_preset

        except Exception:
            return preset


    def has_local_image_settings(self, folder, preset):
        data = setutils.read_local_image_settings(folder)
        return isinstance(data.get(preset.name), dict)


    def _refresh_local_image_status(self, folder):
        folder = Path(folder).resolve()

        item = self.items_by_path.get(str(folder))

        if item is None:
            return

        settings = self.settings_by_item.get(id(item))

        if settings is None:
            settings = self.find_settings_for_path(folder)

        if settings is None:
            return

        root = (
            folder == Path(settings.source_folder).resolve()
        )

        self._invalidate_file_status_cache(folder)
        self._invalidate_folder_cache(folder)

        self.tree.removeItemWidget(item, 1)

        self.tree.setItemWidget(
            item,
            1,
            self.create_folder_status(
                settings,
                folder,
                root=root,
            )
        )


    def edit_local_image(self, settings, preset, folder):

        if settings.mode != "Images":
            return

        folder = Path(folder).resolve()

        local_data = setutils.read_local_image_settings(folder)

        # Start with the global preset.
        effective = Preset.from_dict(preset.to_dict())

        # Apply existing local override.
        current = local_data.get(preset.name)

        if isinstance(current, dict):

            for key, value in current.items():

                if key == "name":
                    continue

                if hasattr(effective, key):
                    setattr(effective, key, value)

        # Local settings always belong to the global preset.
        effective.name = preset.name

        dialog = LocalImageDialog(
            folder,
            effective,
            self,
            enabled_overrides=effective.enabled_overrides,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        if dialog.deleted:
            local_data.pop(preset.name, None)

        else:
            # Never change the preset identity.
            effective.name = preset.name

            local_data[preset.name] = (
                effective.to_dict()
            )

        try:
            setutils.write_local_image_settings(folder, local_data)
        except Exception as exc:
            QMessageBox.critical(self, "Local settings error", str(exc))

        self.mark_dirty()

        self._refresh_local_image_status(folder)


    def get_local_audio_settings(self, folder, preset):
        data = read_local_audio_settings(folder)
        current = data.get(preset.name)
        default_bitrate = int(getattr(preset, "bitrate", 128))
        if not isinstance(current, dict):
            return {"bitrate": default_bitrate}
        bitrate = int(current.get("bitrate", default_bitrate))
        if bitrate not in SUPPORTED_BITRATES:
            bitrate = default_bitrate
        return {"bitrate": bitrate}

    def has_local_audio_settings(self, folder, preset):
        data = read_local_audio_settings(folder)
        return isinstance(data.get(preset.name), dict)

    def edit_local_audio(self, settings, preset, folder):
        if settings.mode != "Audio":
            return
        local_data = read_local_audio_settings(folder)
        current = local_data.get(preset.name)
        bitrate = int(getattr(preset, "bitrate", 128))
        if isinstance(current, dict):
            bitrate = int(current.get("bitrate", bitrate))
        dialog = LocalAudioDialog(preset.output_folder, preset.name, bitrate, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.deleted:
            local_data.pop(preset.name, None)
        else:
            local_data[preset.name] = {"bitrate": dialog.bitrate}
        try:
            write_local_audio_settings(folder, local_data)
        except Exception as exc:
            QMessageBox.critical(self, "Local settings error", str(exc))
            return
        self.mark_dirty()
        self._refresh_local_audio_status(folder)

    def _refresh_local_audio_status(self, folder):
        folder = Path(folder).resolve()

        self._invalidate_audio_status_cache(folder)
        self._invalidate_folder_cache(folder)

        item = self.items_by_path.get(str(folder))

        if item is None:
            return

        settings = (
            self.settings_by_item.get(id(item))
            or self.find_settings_for_path(folder)
        )

        if settings is None:
            return

        root = folder == Path(settings.source_folder).resolve()

        self.tree.removeItemWidget(item, 1)

        self.tree.setItemWidget(
            item,
            1,
            self.create_folder_status(
                settings,
                folder,
                root=root,
            )
        )

    def get_local_preset(self, folder, preset):
        data = setutils.read_local_webm_settings(folder)
        return data.get(preset.name)


    def edit_local_webm(self, settings, preset, folder):

        if settings.mode != "WebM":
            return

        local_data = setutils.read_local_webm_settings(folder)
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

        try:
            setutils.write_local_webm_settings(folder, local_data)
        except Exception as exc:
            QMessageBox.critical(self, "Local settings error", str(exc))

        self.mark_dirty()

        self._refresh_local_webm_status(folder)


    def _refresh_local_webm_status(self, folder):
        folder = Path(folder).resolve()

        item = self.items_by_path.get(str(folder))

        if item is None:
            return

        settings = self.settings_by_item.get(id(item))

        if settings is None:
            settings = self.find_settings_for_path(folder)

        if settings is None:
            return

        root = folder == Path(settings.source_folder).resolve()

        self.tree.removeItemWidget(item, 1)

        self.tree.setItemWidget(
            item,
            1,
            self.create_folder_status(
                settings,
                folder,
                root=root,
            )
        )


    def open_conversion_result(self, settings, preset, folder):
        folder = Path(folder).resolve()

        if settings.mode == "Images":

            if not preset.output_folder:
                return

            local_path = folder.relative_to(settings.source_folder)
            path = preset.output_folder / local_path

            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            else:
                QMessageBox.information(
                    self,
                    "Output not found",
                    f"Output folder does not exist yet:\n{path}"
                )

        elif settings.mode == "Audio":
            path = self.get_audio_output_folder(settings, preset, folder)
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            else:
                QMessageBox.information(
                    self,
                    "Output not found",
                    f"Output folder does not exist yet:\n{path}"
                )

        else:
            local = self.get_local_preset(folder, preset)

            output = WebMConverter(
                folder,
                preset,
                local,
                source_root=settings.source_folder
            ).get_output_file()

            if output.exists():
                self.open_file(output)
            else:
                QMessageBox.information(
                    self,
                    "WebM not found",
                    f"WebM file does not exist yet:\n{output}"
                )


    def start_thumbnail_worker(self, folder=None):
        """
        Start creating thumbnails in parallel.
        Root folders never receive thumbnails.
        """

        paths = []

        if folder:
            folder = Path(folder).resolve()

            item = self.items_by_path.get(str(folder))

            # Root folders don't get thumbnails.
            if item is not None and item.parent() is None:
                return

            try:
                entries = list(folder.iterdir())
            except OSError:
                entries = []

            for path in entries:
                if path.is_file() and path.suffix.lower() in WebMConverter.IMAGE_EXTENSIONS:
                    paths.append((str(path.resolve()), path))

            if item is not None:
                source = self.get_folder_thumbnail_source(folder)

                if source:
                    paths.append((str(folder), source))

                child_folders = sorted(
                    (
                        path for path in entries
                        if path.is_dir()
                    ),
                    key=lambda path: path.name.lower(),
                )

                for child_folder in child_folders:
                    child_item = self.items_by_path.get(str(child_folder.resolve()))

                    if child_item is None:
                        continue

                    child_source = self.get_folder_thumbnail_source(
                        child_folder
                    )

                    if child_source:
                        paths.append((str(child_folder.resolve()), child_source))

        else:
            for path in self.items_by_path:
                item_path = Path(path)

                if item_path.suffix.lower() in WebMConverter.IMAGE_EXTENSIONS:
                    paths.append((path, item_path))
                    continue

                if item_path.is_dir():
                    item = self.items_by_path.get(path)

                    if item is None or item.parent() is None:
                        continue

                    source = self.get_folder_thumbnail_source(item_path)

                    if source:
                        paths.append((path, source))

        if not paths:
            return

        worker = ThumbnailWorker(paths)

        self.thumbnail_worker = worker

        worker.thumbnail_ready.connect(self.thumbnail_ready)
        worker.finished.connect(lambda worker=worker: self.thumbnail_finished(worker))

        worker.start()


    def thumbnail_ready(self, path, cache_path):
        """
        Update thumbnail image.

        QPixmap is created in the GUI thread. Worker threads only create
        the cached PNG file.
        """

        item = self.items_by_path.get(str(Path(path).resolve()))

        if item is None:
            return

        pixmap = QPixmap(str(cache_path))

        if not pixmap.isNull():
            item.setIcon(0, QIcon(pixmap))


    def thumbnail_finished(self, worker=None):
        """
        Release a completed ThumbnailWorker.
        """

        if worker is None:
            worker = self.thumbnail_worker

        if worker is self.thumbnail_worker:
            self.thumbnail_worker = None

        worker.deleteLater()


    def open_file(self, path):

        path = Path(path)

        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


    ############################################################################
    ## Folders Tree

    def open_settings(self, settings):

        dialog = AudioAwareSettingsDialog(settings, self)
        dialog.remove_requested.connect(self.remove_root_folder)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.mark_dirty()
            self.rebuild_folders()


    def remove_root_folder(self, settings):

        if settings not in self.folders:
            return

        result = QMessageBox.question(self, "Remove root folder", "Are you sure?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

        if result != QMessageBox.StandardButton.Yes:
            return

        self.folders.remove(settings)
        self.mark_dirty()
        self.rescan()
        self.log_message(f"Removed from project: {settings.source_folder}")


    def get_tree_state(self):
        """
        Вернуть состояние дерева.
        """

        return {
            'expanded' : { path for path, item in self.items_by_path.items() if item.isExpanded() },
            'vertical_scroll' : self.tree.verticalScrollBar().value(), # type: ignore
            'horizontal_scroll' : self.tree.horizontalScrollBar().value() # type: ignore
        }


    def restore_tree_state(self, state):

        for path in sorted(state['expanded']):
            item = self.items_by_path.get(path)

            if item is not None:
                item.setExpanded(True)

        def set_pos(vertical_scroll, horizontal_scroll):
            self.tree.verticalScrollBar().setValue(max(0, int(vertical_scroll))) # type: ignore
            self.tree.horizontalScrollBar().setValue(max(0, int(horizontal_scroll))) # type: ignore

        QTimer.singleShot(0, lambda: set_pos(state['vertical_scroll'], state['horizontal_scroll']))


    def rescan(self, changed_paths=None):

        if self.rescan_worker and self.rescan_worker.isRunning():
            return

        if changed_paths is None:
            mode = "manual"
            changed_paths = set()
        else:
            mode = "incremental"
            changed_paths = { str(Path(p).resolve()) for p in changed_paths }

        self.tree.setEnabled(False)
        self.tree.setCursor(Qt.CursorShape.WaitCursor)

        self.rescan_button.setEnabled(False)

        if mode == "incremental":
            self.log_message(f"Rescan started for {len(changed_paths)} converted folder(s)...")
        else:
            self.log_message("Rescan started...")


        self.rescan_worker = RescanWorker(self.folders, mode, changed_paths)
        self.rescan_worker.result_ready.connect(self.__rescan_ready)
        self.rescan_worker.error.connect(self.__rescan_error)
        self.rescan_worker.finished.connect(self.__rescan_thread_finished)
        self.rescan_worker.start()


    def __rescan_thread_finished(self):
        worker = self.rescan_worker
        self.rescan_worker = None

        if worker:
            worker.deleteLater()


    def __rescan_ready(self, mode, changed_paths, result):

        try:
            snapshots = result["snapshots"]

            if mode == "manual":
                changed = {
                    path
                    for path, old in self._directory_snapshot.items()
                    if snapshots.get(path) != old
                }

                # New directories are also changes.
                changed.update(
                    path
                    for path in snapshots
                    if path not in self._directory_snapshot
                )

                # Deleted directories are changes too.
                changed.update(
                    path
                    for path in self._directory_snapshot
                    if path not in snapshots
                )

                if changed:
                    self.__incremental_rescan(changed)
                else:
                    self.log_message("Rescan: no filesystem changes.")

                self._directory_snapshot = snapshots

            elif mode == "incremental":
                # Conversion already tells us exactly which source folders
                # changed, so refresh only those folders in the tree.
                self.__incremental_rescan(changed_paths)

                # Merge the freshly scanned subset into the global snapshot.
                # Do not replace the full snapshot with an incremental one.
                for path, signature in snapshots.items():
                    self._directory_snapshot[path] = signature

                scanned = set(result.get("watched", set()))

                # Remove stale snapshot entries below scanned folders.
                for old_path in list(self._directory_snapshot):
                    if any(
                        old_path == root
                        or old_path.startswith(root + os.sep)
                        for root in scanned
                    ) and old_path not in snapshots:
                        del self._directory_snapshot[old_path]

            else:
                self.rebuild_folders()
                self._directory_snapshot = snapshots

            self.log_message("Rescan finished.")

        except Exception as exc:
            self.__rescan_error(str(exc))

        finally:
            self.__finish_rescan()


    def __rescan_error(self, message):
        self.log_message(f"Rescan ERROR: {message}")
        QMessageBox.critical(self, "Rescan error", message)
        self.__finish_rescan()


    def __finish_rescan(self):

        self.tree.setCursor(Qt.CursorShape.ArrowCursor)
        self.tree.setEnabled(True)
        self.rescan_button.setEnabled(True)

        self.update_conversion_button_states()

        # После Stop никогда не продолжаем очередь.
        if self._conversion_stop_requested:
            self._conversion_queue.clear()
            self._active_conversion_job = None
            self.convert_all_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            return

        if self._conversion_queue and self.conversion_worker is None:
            QTimer.singleShot(0, self.start_next_conversion)
        else:
            # Очередь закончилась.
            self.convert_all_button.setEnabled(True)


    def rebuild_folders(self):
        """
        """

        state = self.get_tree_state()

        self.items_by_path.clear()
        self.settings_by_item.clear()
        self._folder_status_cache.clear()
        self._file_status_cache.clear()
        self.tree.clear()

        for settings in self.folders:
            self.add_folder_item(settings)

        if state:
            self.restore_tree_state(state)


    def __incremental_rescan(self, changed_paths):

        for changed in changed_paths:
            path = Path(changed).resolve()

            if path.is_file():
                path = path.parent

            self._refresh_tree_folder(path)


    def _refresh_folder_status_widgets(self, folders):
        """
        Refresh status widgets for the specified folders and all their
        parent folders inside the corresponding project root.
        """

        affected = set()

        for folder in folders:
            folder = Path(folder).resolve()

            settings = self.find_settings_for_path(folder)

            if settings is None:
                continue

            root = Path(settings.source_folder).resolve()

            current = folder

            while True:
                if current.is_dir():
                    affected.add(current)

                if current == root:
                    break

                if root not in current.parents:
                    break

                current = current.parent

        for folder in affected:
            item = self.items_by_path.get(str(folder))

            if item is None:
                continue

            settings = self.settings_by_item.get(id(item))

            if settings is None:
                settings = self.find_settings_for_path(folder)

            if settings is None:
                continue

            self._invalidate_file_status_cache(folder)
            self._invalidate_folder_cache(folder)

            # Column 1 contains the folder status widget.
            self.tree.removeItemWidget(item, 1)
            self.tree.setItemWidget(
                item,
                1,
                self.create_folder_status(
                    settings,
                    folder,
                    root=(folder == Path(settings.source_folder).resolve())
                )
            )

    def _refresh_tree_folder(self, folder):
        folder = Path(folder).resolve()
        settings = self.find_settings_for_path(folder)

        if settings is None:
            return

        item = self.items_by_path.get(str(folder))

        if not folder.exists():

            if item is not None:
                parent = item.parent()

                self._remove_tree_item(item)

                if parent is not None:
                    self._invalidate_folder_cache(Path(parent.data(0, Qt.ItemDataRole.UserRole)))

            return

        if not folder.is_dir():
            return

        if item is None:
            parent_folder = folder.parent
            parent_item = self.items_by_path.get(str(parent_folder))

            if parent_item is not None:
                self._refresh_tree_folder(parent_folder)

            return

        expanded = item.isExpanded()

        if not item.isExpanded():
            self._invalidate_file_status_cache(folder)
            self._invalidate_folder_cache(folder)
            return

        while item.childCount():
            self._remove_tree_item(item.child(0))

        FOLDER_LOADED_ROLE = Qt.ItemDataRole.UserRole + 1

        item.setData(0, FOLDER_LOADED_ROLE, False)

        self._populate_folder_children(item, folder, settings)

        item.setData(0, FOLDER_LOADED_ROLE, True)

        item.setExpanded(expanded)

        # Thumbnail только для этой папки.
        self.start_thumbnail_worker(folder)


    def _remove_tree_item(self, item):

        for index in range(item.childCount() - 1, -1, -1):
            self._remove_tree_item(item.child(index))

        path = item.data(0, Qt.ItemDataRole.UserRole)

        if path:
            path = str(Path(path).resolve())
            self.items_by_path.pop(path, None)

        self.settings_by_item.pop(id(item), None)

        parent = item.parent()

        if parent is not None:
            parent.removeChild(item)
        else:
            index = self.tree.indexOfTopLevelItem(item)

            if index >= 0:
                self.tree.takeTopLevelItem(index)


    ############################################################################
    ## Conversion

    def start_conversion(self, settings, preset, folder):

        if not preset.output_folder:
            QMessageBox.warning(
                self,
                "Output folder",
                f"Preset '{preset.name}' has no Output folder."
            )
            return

        if settings.mode == "Audio":
            folder = Path(folder).resolve()
            self.enqueue_conversion((settings, preset, folder, self.get_local_audio_settings(folder, preset)))
            return

        if settings.mode == "WebM":
            folders = self.get_webm_folders(Path(folder))

            for folder in folders:
                effective_preset = self.get_local_image_preset(folder, preset)

                self.enqueue_conversion((
                    settings,
                    preset,
                    folder,
                    self.get_local_preset(folder, preset)))

        else:
            folder = Path(folder).resolve()

            effective_preset = self.get_local_image_preset(folder, preset)

            self.enqueue_conversion((
                settings,
                effective_preset,
                folder,
                None,
            ))


    def start_folder_all_conversions(self, settings, folder):

        jobs = []

        folder = Path(folder).resolve()

        for preset in settings.presets:

            if not preset.output_folder:
                continue

            if settings.mode == "WebM":
                job_preset = preset
                local = self.get_local_preset(folder, preset)

            elif settings.mode == "Audio":
                job_preset = preset
                local = self.get_local_audio_settings(folder, preset)

            else:
                job_preset = self.get_local_image_preset(folder, preset)
                local = None

            jobs.append((settings, job_preset, folder, local))

        if not jobs:
            QMessageBox.information(
                self,
                "Convert All",
                "This folder has no configured presets."
            )
            return

        for job in jobs:
            self.enqueue_conversion(job)


    def start_all_conversions(self):

        jobs = []

        for settings in self.folders:

            for preset in settings.presets:

                if not preset.output_folder:
                    continue

                if settings.mode == "WebM":
                    folders = self.get_webm_folders(Path(settings.source_folder))
                else:
                    folders = [ Path(settings.source_folder) ]

                for folder in folders:
                    folder = Path(folder).resolve()

                    if settings.mode == "WebM":
                        job_preset = preset
                        local = self.get_local_preset(folder, preset)
                    elif settings.mode == "Audio":
                        job_preset = preset
                        local = self.get_local_audio_settings(folder, preset)
                    else:
                        job_preset = self.get_local_image_preset(folder, preset)
                        local = None

                    jobs.append((settings, job_preset, folder, local))

        if not jobs:
            QMessageBox.information(
                self,
                "Convert All",
                "No configured conversions found."
            )
            return

        for job in jobs:
            self.enqueue_conversion(job)


    def get_webm_folders(self, root):
        root = Path(root).resolve()
        folders = []

        def scan(folder):
            try:
                entries = list(folder.iterdir())
            except OSError:
                return

            child_folders = [ entry for entry in entries if entry.is_dir() ]

            has_images = any(
                entry.is_file()
                and entry.suffix.lower() in WebMConverter.IMAGE_EXTENSIONS
                for entry in entries
            )

            # Конечная папка:
            # есть картинки и нет подпапок
            if has_images and not child_folders:
                folders.append(folder)
                return

            for child in sorted(child_folders, key=lambda p: p.name.lower()):
                scan(child)

        scan(root)
        return folders


    def start_covnersion_jobs(self, jobs):
        if jobs and jobs[0][0].mode == "Audio":
            self.conversion_worker = AudioConversionWorker(jobs)
        else:
            self.conversion_worker = ConversionWorker(jobs)
        self.conversion_worker.message.connect(self.log_message)
        self.conversion_worker.error.connect(self.conversion_error)
        self.conversion_worker.progress.connect(self.update_progress)
        self.conversion_worker.finished_signal.connect(self.conversion_finished)
        self.conversion_worker.finished.connect(self.conversion_worker_finished)
        self.convert_all_button.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Preparing...")
        self.conversion_worker.start()


    def conversion_worker_finished(self):

        worker = self.conversion_worker
        self.conversion_worker = None

        if worker:
            worker.deleteLater()

        changed_folders = {
            Path(folder).resolve()
            for folder in self._conversion_changed_folders
        }

        self._conversion_changed_folders.clear()

        # Текущая задача закончилась.
        self._active_conversion_job = None
        self.update_conversion_button_states()

        if changed_folders:
            self._refresh_folder_status_widgets(changed_folders)
            self.rescan(changed_folders)
        else:
            self.rescan()


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
            self.progress.setFormat(f"{done}/{total} • {percent}% • ETA: {textutils.format_eta(eta)}")


    def stop_conversion(self):

        # Отменяем не только текущую конвертацию,
        # но и все задания, ожидающие в очереди.
        self._conversion_stop_requested = True
        self._conversion_queue.clear()

        self.log_message("Stopping conversion queue...")
        self.stop_button.setEnabled(False)

        if self.conversion_worker is not None:
            self.conversion_worker.stop()

        self.update_conversion_button_states()


    def conversion_error(self, message):
        self.log_message(f"ERROR: {message}")
        QMessageBox.critical(self, "Conversion error", message)


    def conversion_finished(self, changed_folders):
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setFormat("Done")

        # Не включаем Convert All здесь.
        # Очередь ещё может содержать задания.
        self.stop_button.setEnabled(False)

        self._conversion_changed_folders = changed_folders


    ############################################################################
    ## Project

    def get_project_data(self):
        return {
            "version": self.PROJECT_VERSION,
            "folders": [settings.to_dict() for settings in self.folders],
            "ui_state": self.get_tree_state()
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

            setutils.write_last_project(path)
            self.project_filename = str(path)
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

            self.folders.clear()
            self.items_by_path.clear()
            self.settings_by_item.clear()
            self.tree.clear()

            for folder_data in data.get("folders", []):
                settings = FolderSettings.from_dict(folder_data)

                if settings.source_folder:
                    self.folders.append(settings)

            setutils.write_last_project(path)
            self.project_filename = str(path)
            self.mark_clean()
            self.rescan()
            self.log_message(f"Project loaded: {path}")

        except Exception as exc:
            QMessageBox.critical(self, "Load error", str(exc))


    def load_last_project(self):
        filename = setutils.read_last_project()

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

        if not self.project_is_dirty:
            return True

        result = QMessageBox.question(self, "Unsaved changes", "The project has unsaved changes. Save them?", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)

        if result == QMessageBox.StandardButton.Save:
            self.save_project()
            return not self.project_is_dirty

        if result == QMessageBox.StandardButton.Discard:
            return True

        return False


    ############################################################################


    def log_message(self, message):
        self.log.append(str(message))


    def closeEvent(self, event):

        if self.conversion_worker:
            QMessageBox.warning(
                self,
                "Conversion running",
                "Stop the conversion before closing."
            )
            event.ignore()
            return

        if self.rescan_worker and self.rescan_worker.isRunning():
            self.rescan_worker.terminate()

        if self.thumbnail_worker and self.thumbnail_worker.isRunning():
            self.thumbnail_worker.terminate()

        if self.confirm_save():
            event.accept()
        else:
            event.ignore()
