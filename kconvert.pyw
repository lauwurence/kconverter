
import sys
import os
import json
import pickle
import hashlib
import ctypes
import re
import subprocess
from io import BytesIO
from time import time
from threading import Event, Thread
from pathlib import Path
from datetime import datetime
import ffmpeg
from PIL import Image, ImageCms, ImageFilter
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QSize, QTimer, QFileSystemWatcher
from PyQt6.QtGui import QShortcut, QKeySequence, QIcon, QDesktopServices, QFont, QPixmap
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget, QTreeWidgetItem, QFileDialog, QTextEdit, QProgressBar, QDialog, QDialogButtonBox, QFormLayout, QComboBox, QLineEdit, QMessageBox, QSpinBox, QDoubleSpinBox, QHeaderView, QListWidget, QListWidgetItem, QLabel, QCheckBox, QToolButton

VERSION = (0, 1)
LOCAL_DIR = Path(__file__).resolve().parent
SAVES_DIR = LOCAL_DIR / "saves"
PERSISTENT_FILE = LOCAL_DIR / "persistent"
THUMBNAIL_DIR = LOCAL_DIR / ".thumbnails"
PROJECT_EXTENSION = ".kcp"
LOCAL_WEBM_FILE = ".kconverter_webm.json"
WEBM_CACHE_FILE = ".kconverter_webm_cache.json"
THUMBNAIL_SIZE = 56
ROOT_ROW_HEIGHT = 64
FOLDER_ROW_HEIGHT = 32
ICON = "icons/icon.svg"
APPID = f'kconverter.{".".join([str(v) for v in VERSION])}'.lower()
PROFILE_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
RESAMPLE = Image.Resampling.LANCZOS
MINTERPOLATE = {
    1: "minterpolate=fps={}:mi_mode=blend",
    2: "minterpolate=fps={}",
    3: "minterpolate=fps={}:mi_mode=mci:mc_mode=aobmc:me_mode=bilat",
    4: "minterpolate=fps={}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir",
    5: "minterpolate=fps={}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1",
    6: "minterpolate=fps={}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=fdiff",
}

def default_webm_settings():
    return {
        "input_fps": 48,
        "output_fps": 60,
        "speed": 1.0,
        "resize_mode": "Resolution",
        "resolution_width": 1920,
        "resolution_height": 1080,
        "downsample": 1.0,
        "resolution": "1920x1080",
        "cpu_used": 2,
        "threads": 0,
        "row_mt": 1,
        "tile_columns": 2,
        "tile_rows": 0,
        "crf": 15,
        "image_quality": 90,
        "sharpen": 0.0,
        "interpolate": 4,
        "loop": True,
        "reverse": False,
        "pix_fmt": "yuv420p",
        "codec": "libvpx-vp9",
    }

def parse_resolution(value):
    if not isinstance(value, str) or value == "Original":
        return None
    match = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", value, re.IGNORECASE)
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width < 1 or height < 1:
        return None
    return width, height

def normalize_webm_settings(data):
    settings = default_webm_settings()
    settings.update(data or {})
    old_resolution = parse_resolution(settings.get("resolution", ""))
    if "resize_mode" not in (data or {}):
        if old_resolution:
            settings["resize_mode"] = "Resolution"
            settings["resolution_width"], settings["resolution_height"] = old_resolution
        else:
            settings["resize_mode"] = "Downsample"
            settings["downsample"] = 1.0
    if settings.get("resize_mode") not in {"Resolution", "Downsample"}:
        settings["resize_mode"] = "Resolution"
    settings["resolution_width"] = max(1, int(settings.get("resolution_width", 1920)))
    settings["resolution_height"] = max(1, int(settings.get("resolution_height", 1080)))
    settings["downsample"] = max(0.01, float(settings.get("downsample", 1.0)))
    if settings["resize_mode"] == "Resolution":
        settings["resolution"] = f'{settings["resolution_width"]}x{settings["resolution_height"]}'
    else:
        settings["resolution"] = "Original"
    return settings

class Preset:
    def __init__(self, name="New Preset", downscale=1.0, target_size=400, max_quality=95, min_quality=50, output_folder="", suffix="", sharpen_radius=0.5, sharpen_percent=0, sharpen_threshold=3, webm=None, resize_mode="Downsample", resolution_width=0, resolution_height=0):
        self.name = name
        self.resize_mode = resize_mode
        self.resolution_width = int(resolution_width)
        self.resolution_height = int(resolution_height)
        self.downscale = float(downscale)
        self.target_size = target_size
        self.max_quality = max_quality
        self.min_quality = min_quality
        self.output_folder = output_folder
        self.suffix = suffix
        self.sharpen_radius = sharpen_radius
        self.sharpen_percent = sharpen_percent
        self.sharpen_threshold = sharpen_threshold
        self.webm = normalize_webm_settings(webm or {})

    def to_dict(self):
        return {
            "name": self.name,
            "resize_mode": self.resize_mode,
            "resolution_width": self.resolution_width,
            "resolution_height": self.resolution_height,
            "downscale": self.downscale,
            "target_size": self.target_size,
            "max_quality": self.max_quality,
            "min_quality": self.min_quality,
            "output_folder": self.output_folder,
            "suffix": self.suffix,
            "sharpen_radius": self.sharpen_radius,
            "sharpen_percent": self.sharpen_percent,
            "sharpen_threshold": self.sharpen_threshold,
            "webm": self.webm,
        }

    @classmethod
    def from_dict(cls, data):
        resize_mode = data.get("resize_mode")
        resolution_width = int(data.get("resolution_width", 0))
        resolution_height = int(data.get("resolution_height", 0))
        if resize_mode not in {"Resolution", "Downsample"}:
            resize_mode = "Downsample"
        return cls(
            data.get("name", "New Preset"),
            float(data.get("downscale", 1.0)),
            int(data.get("target_size", 400)),
            int(data.get("max_quality", 95)),
            int(data.get("min_quality", 50)),
            data.get("output_folder", ""),
            data.get("suffix", ""),
            float(data.get("sharpen_radius", 0.5)),
            int(data.get("sharpen_percent", 0)),
            int(data.get("sharpen_threshold", 3)),
            data.get("webm", {}),
            resize_mode,
            resolution_width,
            resolution_height,
        )

class FolderSettings:
    def __init__(self, source_folder, mode="Images"):
        self.source_folder = str(Path(source_folder).resolve())
        self.mode = mode
        self.presets = [
            Preset("Mobile", 3.0, 200, 90, 50, "", "@0.667"),
            Preset("1080p", 2.0, 400, 95, 50, "", ""),
            Preset("4K", 1.0, 800, 100, 50, "", "@2"),
        ]
        for preset in self.presets:
            preset.webm = default_webm_settings()

    def to_dict(self):
        return {"source_folder": self.source_folder, "mode": self.mode, "presets": [preset.to_dict() for preset in self.presets]}

    @classmethod
    def from_dict(cls, data):
        settings = cls(data.get("source_folder", ""), data.get("mode", "Images"))
        settings.presets = [Preset.from_dict(preset) for preset in data.get("presets", [])]
        for preset in settings.presets:
            preset.webm = normalize_webm_settings(preset.webm)
        return settings

class ResizeControls:
    def __init__(self, form, resize_mode="Downsample", width=1920, height=1080, downsample=1.0):
        self.resize_mode = QComboBox()
        self.resize_mode.addItems(["Resolution", "Downsample"])
        self.resize_mode.setCurrentText(resize_mode)
        self.resolution_widget = QWidget()
        resolution_layout = QHBoxLayout(self.resolution_widget)
        resolution_layout.setContentsMargins(0, 0, 0, 0)
        resolution_layout.setSpacing(4)
        self.resolution_width = QSpinBox()
        self.resolution_width.setRange(1, 999999)
        self.resolution_width.setValue(max(1, width))
        self.resolution_width.setSuffix(" px")
        self.resolution_height = QSpinBox()
        self.resolution_height.setRange(1, 999999)
        self.resolution_height.setValue(max(1, height))
        self.resolution_height.setSuffix(" px")
        resolution_layout.addWidget(self.resolution_width)
        resolution_layout.addWidget(self.resolution_height)
        self.downsample_widget = QWidget()
        downsample_layout = QHBoxLayout(self.downsample_widget)
        downsample_layout.setContentsMargins(0, 0, 0, 0)
        downsample_layout.setSpacing(4)
        self.downsample = QDoubleSpinBox()
        self.downsample.setRange(0.01, 100.0)
        self.downsample.setDecimals(2)
        self.downsample.setSingleStep(0.1)
        self.downsample.setValue(max(0.01, downsample))
        self.downsample.setSuffix("×")
        downsample_layout.addWidget(self.downsample)
        self.container = QWidget()
        container_layout = QHBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(4)
        container_layout.addWidget(self.resolution_widget)
        container_layout.addWidget(self.downsample_widget)
        form.addRow("Resize:", self.resize_mode)
        form.addRow("", self.container)
        self.resize_mode.currentTextChanged.connect(self.update_visibility)
        self.update_visibility(self.resize_mode.currentText())

    def update_visibility(self, mode):
        self.resolution_widget.setVisible(mode == "Resolution")
        self.downsample_widget.setVisible(mode == "Downsample")

    def values(self):
        return {
            "resize_mode": self.resize_mode.currentText(),
            "resolution_width": self.resolution_width.value(),
            "resolution_height": self.resolution_height.value(),
            "downsample": self.downsample.value(),
        }

class PresetDialog(QDialog):
    def __init__(self, preset, mode, parent=None):
        super().__init__(parent)
        self.preset = preset
        self.mode = mode
        self.setWindowTitle("Preset Settings")
        self.setWindowIcon(QIcon(ICON))
        self.resize(600, 0)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(preset.name)
        form.addRow("Name:", self.name_edit)
        output_layout = QHBoxLayout()
        self.output_edit = QLineEdit(preset.output_folder)
        output_button = QPushButton("Choose")
        output_button.clicked.connect(self.choose_output)
        output_layout.addWidget(self.output_edit)
        output_layout.addWidget(output_button)
        form.addRow("Output folder:", output_layout)
        self.suffix_edit = QLineEdit(preset.suffix)
        self.suffix_edit.setPlaceholderText("Example: @2")
        form.addRow("File suffix:", self.suffix_edit)
        if mode == "Images":
            self.add_images_settings(form)
        else:
            self.add_webm_settings(form)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if folder:
            self.output_edit.setText(folder)

    def add_images_settings(self, form):
        self.resize_controls = ResizeControls(form, self.preset.resize_mode, self.preset.resolution_width or 1920, self.preset.resolution_height or 1080, self.preset.downscale)
        self.target_size = QSpinBox()
        self.target_size.setRange(1, 999999)
        self.target_size.setSuffix(" KB")
        self.target_size.setValue(self.preset.target_size)
        form.addRow("Target size:", self.target_size)
        self.max_quality = QSpinBox()
        self.max_quality.setRange(1, 100)
        self.max_quality.setValue(self.preset.max_quality)
        form.addRow("Max quality:", self.max_quality)
        self.min_quality = QSpinBox()
        self.min_quality.setRange(1, 100)
        self.min_quality.setValue(self.preset.min_quality)
        form.addRow("Min quality:", self.min_quality)
        self.sharpen_radius = QDoubleSpinBox()
        self.sharpen_radius.setRange(0.0, 100.0)
        self.sharpen_radius.setDecimals(2)
        self.sharpen_radius.setSingleStep(0.25)
        self.sharpen_radius.setValue(self.preset.sharpen_radius)
        form.addRow("Sharpen radius:", self.sharpen_radius)
        self.sharpen_percent = QSpinBox()
        self.sharpen_percent.setRange(0, 500)
        self.sharpen_percent.setValue(self.preset.sharpen_percent)
        form.addRow("Sharpen percent:", self.sharpen_percent)
        self.sharpen_threshold = QSpinBox()
        self.sharpen_threshold.setRange(0, 255)
        self.sharpen_threshold.setValue(self.preset.sharpen_threshold)
        form.addRow("Sharpen threshold:", self.sharpen_threshold)

    def add_webm_settings(self, form):
        settings = normalize_webm_settings(self.preset.webm)
        self.add_webm_controls(form, settings)

    def add_webm_controls(self, form, settings):
        self.input_fps = QDoubleSpinBox()
        self.input_fps.setRange(0.1, 1000)
        self.input_fps.setValue(settings["input_fps"])
        form.addRow("Input FPS:", self.input_fps)
        self.output_fps = QDoubleSpinBox()
        self.output_fps.setRange(0.1, 1000)
        self.output_fps.setValue(settings["output_fps"])
        form.addRow("Output FPS:", self.output_fps)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.01, 100)
        self.speed.setDecimals(2)
        self.speed.setValue(settings["speed"])
        form.addRow("Speed:", self.speed)
        self.resize_controls = ResizeControls(form, settings["resize_mode"], settings["resolution_width"], settings["resolution_height"], settings["downsample"])
        self.cpu_used = QSpinBox()
        self.cpu_used.setRange(0, 8)
        self.cpu_used.setValue(settings["cpu_used"])
        form.addRow("CPU used:", self.cpu_used)
        self.threads = QSpinBox()
        self.threads.setRange(0, 128)
        self.threads.setValue(settings["threads"])
        form.addRow("Threads:", self.threads)
        self.row_mt = QSpinBox()
        self.row_mt.setRange(0, 1)
        self.row_mt.setValue(settings["row_mt"])
        form.addRow("Row MT:", self.row_mt)
        self.tile_columns = QSpinBox()
        self.tile_columns.setRange(0, 6)
        self.tile_columns.setValue(settings["tile_columns"])
        form.addRow("Tile columns:", self.tile_columns)
        self.tile_rows = QSpinBox()
        self.tile_rows.setRange(0, 2)
        self.tile_rows.setValue(settings["tile_rows"])
        form.addRow("Tile rows:", self.tile_rows)
        self.crf = QSpinBox()
        self.crf.setRange(0, 63)
        self.crf.setValue(settings["crf"])
        form.addRow("CRF:", self.crf)
        self.image_quality = QSpinBox()
        self.image_quality.setRange(1, 100)
        self.image_quality.setValue(settings["image_quality"])
        form.addRow("Preview quality:", self.image_quality)
        self.sharpen = QDoubleSpinBox()
        self.sharpen.setRange(0, 10)
        self.sharpen.setDecimals(2)
        self.sharpen.setValue(settings["sharpen"])
        form.addRow("Sharpen:", self.sharpen)
        self.interpolate = QSpinBox()
        self.interpolate.setRange(0, 6)
        self.interpolate.setValue(settings["interpolate"])
        form.addRow("Interpolation:", self.interpolate)
        self.loop = QCheckBox()
        self.loop.setChecked(settings["loop"])
        form.addRow("Loop:", self.loop)
        self.reverse = QCheckBox()
        self.reverse.setChecked(settings["reverse"])
        form.addRow("Reverse:", self.reverse)

    def collect_webm(self):
        resize = self.resize_controls.values()
        result = {
            "input_fps": self.input_fps.value(),
            "output_fps": self.output_fps.value(),
            "speed": self.speed.value(),
            **resize,
            "cpu_used": self.cpu_used.value(),
            "threads": self.threads.value(),
            "row_mt": self.row_mt.value(),
            "tile_columns": self.tile_columns.value(),
            "tile_rows": self.tile_rows.value(),
            "crf": self.crf.value(),
            "image_quality": self.image_quality.value(),
            "sharpen": self.sharpen.value(),
            "interpolate": self.interpolate.value(),
            "loop": self.loop.isChecked(),
            "reverse": self.reverse.isChecked(),
            "pix_fmt": "yuv420p",
            "codec": "libvpx-vp9",
        }
        result["resolution"] = f'{resize["resolution_width"]}x{resize["resolution_height"]}' if resize["resize_mode"] == "Resolution" else "Original"
        return result

    def accept(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Invalid preset", "Preset name cannot be empty.")
            return
        self.preset.name = name
        self.preset.output_folder = self.output_edit.text().strip()
        self.preset.suffix = self.suffix_edit.text()
        if self.mode == "Images":
            resize = self.resize_controls.values()
            self.preset.resize_mode = resize["resize_mode"]
            self.preset.resolution_width = resize["resolution_width"]
            self.preset.resolution_height = resize["resolution_height"]
            self.preset.downscale = resize["downsample"]
            self.preset.target_size = self.target_size.value()
            self.preset.max_quality = self.max_quality.value()
            self.preset.min_quality = self.min_quality.value()
            self.preset.sharpen_radius = self.sharpen_radius.value()
            self.preset.sharpen_percent = self.sharpen_percent.value()
            self.preset.sharpen_threshold = self.sharpen_threshold.value()
            if self.preset.min_quality > self.preset.max_quality:
                QMessageBox.warning(self, "Invalid quality", "Min quality cannot be greater than Max quality.")
                return
        else:
            self.preset.webm = self.collect_webm()
        super().accept()

class LocalWebMDialog(QDialog):
    def __init__(self, folder, preset, settings, parent=None):
        super().__init__(parent)
        self.folder = Path(folder).resolve()
        self.preset = preset
        self.settings = normalize_webm_settings(settings)
        self.deleted = False
        self.setWindowTitle(f"Local WebM Settings — {preset.name}")
        self.setWindowIcon(QIcon(ICON))
        self.resize(620, 620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.add_controls(form)
        layout.addLayout(form)
        info = QLabel(f"<small>Folder: {self.folder}<br>Preset: {preset.name}<br>These settings override the global WebM preset only for this folder.</small>")
        info.setWordWrap(True)
        layout.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.delete_button = buttons.addButton("Delete Local Settings", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.delete_button.setEnabled((self.folder / LOCAL_WEBM_FILE).exists())
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.delete_button.clicked.connect(self.delete_local)
        layout.addWidget(buttons)

    def add_controls(self, form):
        s = self.settings
        self.input_fps = QDoubleSpinBox()
        self.input_fps.setRange(0.1, 1000)
        self.input_fps.setValue(s["input_fps"])
        form.addRow("Input FPS:", self.input_fps)
        self.output_fps = QDoubleSpinBox()
        self.output_fps.setRange(0.1, 1000)
        self.output_fps.setValue(s["output_fps"])
        form.addRow("Output FPS:", self.output_fps)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.01, 100)
        self.speed.setDecimals(2)
        self.speed.setValue(s["speed"])
        form.addRow("Speed:", self.speed)
        self.resize_controls = ResizeControls(form, s["resize_mode"], s["resolution_width"], s["resolution_height"], s["downsample"])
        self.cpu_used = QSpinBox()
        self.cpu_used.setRange(0, 8)
        self.cpu_used.setValue(s["cpu_used"])
        form.addRow("CPU used:", self.cpu_used)
        self.threads = QSpinBox()
        self.threads.setRange(0, 128)
        self.threads.setValue(s["threads"])
        form.addRow("Threads:", self.threads)
        self.row_mt = QSpinBox()
        self.row_mt.setRange(0, 1)
        self.row_mt.setValue(s["row_mt"])
        form.addRow("Row MT:", self.row_mt)
        self.tile_columns = QSpinBox()
        self.tile_columns.setRange(0, 6)
        self.tile_columns.setValue(s["tile_columns"])
        form.addRow("Tile columns:", self.tile_columns)
        self.tile_rows = QSpinBox()
        self.tile_rows.setRange(0, 2)
        self.tile_rows.setValue(s["tile_rows"])
        form.addRow("Tile rows:", self.tile_rows)
        self.crf = QSpinBox()
        self.crf.setRange(0, 63)
        self.crf.setValue(s["crf"])
        form.addRow("CRF:", self.crf)
        self.image_quality = QSpinBox()
        self.image_quality.setRange(1, 100)
        self.image_quality.setValue(s["image_quality"])
        form.addRow("Preview quality:", self.image_quality)
        self.sharpen = QDoubleSpinBox()
        self.sharpen.setRange(0, 10)
        self.sharpen.setDecimals(2)
        self.sharpen.setValue(s["sharpen"])
        form.addRow("Sharpen:", self.sharpen)
        self.interpolate = QSpinBox()
        self.interpolate.setRange(0, 6)
        self.interpolate.setValue(s["interpolate"])
        form.addRow("Interpolation:", self.interpolate)
        self.loop = QCheckBox()
        self.loop.setChecked(s["loop"])
        form.addRow("Loop:", self.loop)
        self.reverse = QCheckBox()
        self.reverse.setChecked(s["reverse"])
        form.addRow("Reverse:", self.reverse)

    def collect(self):
        resize = self.resize_controls.values()
        result = {
            "input_fps": self.input_fps.value(),
            "output_fps": self.output_fps.value(),
            "speed": self.speed.value(),
            **resize,
            "cpu_used": self.cpu_used.value(),
            "threads": self.threads.value(),
            "row_mt": self.row_mt.value(),
            "tile_columns": self.tile_columns.value(),
            "tile_rows": self.tile_rows.value(),
            "crf": self.crf.value(),
            "image_quality": self.image_quality.value(),
            "sharpen": self.sharpen.value(),
            "interpolate": self.interpolate.value(),
            "loop": self.loop.isChecked(),
            "reverse": self.reverse.isChecked(),
            "pix_fmt": "yuv420p",
            "codec": "libvpx-vp9",
        }
        result["resolution"] = f'{resize["resolution_width"]}x{resize["resolution_height"]}' if resize["resize_mode"] == "Resolution" else "Original"
        return result

    def delete_local(self):
        result = QMessageBox.question(self, "Delete local settings", f"Delete local WebM settings for '{self.preset.name}' in:\n{self.folder}?")
        if result == QMessageBox.StandardButton.Yes:
            self.deleted = True
            self.accept()

    def accept(self):
        if not self.deleted:
            self.settings = self.collect()
        super().accept()

class SettingsDialog(QDialog):
    remove_requested = pyqtSignal(object)
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(f"Folder Settings [{settings.source_folder}]")
        self.setWindowIcon(QIcon(ICON))
        self.resize(400, 350)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Images", "WebM"])
        self.mode_combo.setCurrentText(settings.mode)
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
        form.addRow("Mode:", self.mode_combo)
        layout.addLayout(form)
        layout.addWidget(QLabel("<b>Presets</b>"))
        self.preset_list = QListWidget()
        self.preset_list.itemDoubleClicked.connect(self.edit_preset)
        layout.addWidget(self.preset_list)
        buttons_layout = QHBoxLayout()
        add_button = QPushButton("Add")
        remove_button = QPushButton("Remove")
        add_button.clicked.connect(self.add_preset)
        remove_button.clicked.connect(self.remove_preset)
        buttons_layout.addWidget(add_button)
        buttons_layout.addWidget(remove_button)
        buttons_layout.addStretch()
        layout.addLayout(buttons_layout)
        remove_root_button = QPushButton("Remove Folder")
        remove_root_button.setStyleSheet("QPushButton { color: #f00; }")
        remove_root_button.setToolTip("Remove this root folder from the project. Files on disk will not be deleted.")
        remove_root_button.clicked.connect(self.remove_root_folder)
        layout.addWidget(remove_root_button)
        dialog_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        dialog_buttons.accepted.connect(self.accept)
        dialog_buttons.rejected.connect(self.reject)
        layout.addWidget(dialog_buttons)
        self.refresh_preset_list()

    def refresh_preset_list(self):
        self.preset_list.clear()
        for preset in self.settings.presets:
            item = QListWidgetItem(preset.name)
            item.setData(Qt.ItemDataRole.UserRole, preset)
            self.preset_list.addItem(item)

    def on_mode_changed(self, new_mode):
        if new_mode == self.settings.mode:
            return
        self.settings.mode = new_mode
        for preset in self.settings.presets:
            preset.webm = normalize_webm_settings(preset.webm)
        self.refresh_preset_list()

    def add_preset(self):
        preset = Preset()
        preset.webm = default_webm_settings()
        if PresetDialog(preset, self.settings.mode, self).exec() == QDialog.DialogCode.Accepted:
            self.settings.presets.append(preset)
            self.refresh_preset_list()

    def edit_preset(self, item=None):
        if item is None:
            item = self.preset_list.currentItem()
        if item is None:
            return
        preset = item.data(Qt.ItemDataRole.UserRole)
        if PresetDialog(preset, self.settings.mode, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh_preset_list()

    def remove_preset(self):
        item = self.preset_list.currentItem()
        if item is None:
            return
        preset = item.data(Qt.ItemDataRole.UserRole)
        result = QMessageBox.question(self, "Remove Preset", f'Remove preset "{preset.name}"?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if result == QMessageBox.StandardButton.Yes:
            self.settings.presets.remove(preset)
            self.refresh_preset_list()

    def remove_root_folder(self):
        result = QMessageBox.question(self, "Remove Folder", f"Remove this folder from the project?\n\n{self.settings.source_folder}\n\nThe folder and its files will NOT be deleted from disk.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if result != QMessageBox.StandardButton.Yes:
            return
        self.remove_requested.emit(self.settings)
        self.reject()

    def accept(self):
        super().accept()

class ThumbnailWorker(QThread):
    thumbnail_ready = pyqtSignal(str, object)
    def __init__(self, items):
        super().__init__()
        self.items = items
        self.stop_event = Event()

    def stop(self):
        self.stop_event.set()

    def cache_file(self, path):
        try:
            stat = path.stat()
            key = f"{path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}".encode()
        except OSError:
            key = str(path.resolve()).encode()
        return THUMBNAIL_DIR / f"{hashlib.sha1(key).hexdigest()}.png"

    def create_thumbnail(self, path):
        cache = self.cache_file(path)
        if cache.exists():
            pixmap = QIcon(str(cache)).pixmap(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
            if not pixmap.isNull():
                return pixmap
        try:
            with Image.open(path) as image:
                image.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                image.save(buffer, "PNG")
                pixmap = QPixmap()
                pixmap.loadFromData(buffer.getvalue(), "PNG")
                if pixmap.isNull():
                    return None
                cache.parent.mkdir(parents=True, exist_ok=True)
                pixmap.save(str(cache), "PNG")
                return pixmap
        except Exception:
            return None

    def run(self):
        THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
        for item_path, thumbnail_source in self.items:
            if self.stop_event.is_set():
                break
            pixmap = self.create_thumbnail(thumbnail_source)
            if pixmap:
                self.thumbnail_ready.emit(item_path, pixmap)

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

class ImageConverter:
    INPUT_SUFFIXES = {".png"}
    IGNORE_FOLDERS = {"backup"}
    CACHE_FILE = ".cache"
    RESAMPLE = Image.Resampling.LANCZOS
    PROFILE_SRGB = PROFILE_SRGB
    REGEX_NUMBER = re.compile(r".*?(\d*)_(\d*)")
    REGEX_QUALITY = re.compile(r"\s*(\d*)%")
    EXIF_DATA = {315: "keyclap", 33432: f"Copyright {datetime.now().year} keyclap. All Rights Reserved."}

    def __init__(self, source, preset, stop_event=None, progress_callback=None, source_root=None):
        self.source = Path(source).resolve()
        self.source_root = Path(source_root).resolve() if source_root else self.source
        self.preset = preset
        self.resize_mode = preset.resize_mode
        self.resolution_width = preset.resolution_width
        self.resolution_height = preset.resolution_height
        self.downscale = preset.downscale
        self.target_size = preset.target_size
        self.maximum_quality = preset.max_quality
        self.minimum_quality = preset.min_quality
        self.sharpen_radius = preset.sharpen_radius
        self.sharpen_percent = preset.sharpen_percent
        self.sharpen_threshold = preset.sharpen_threshold
        self.output = self.resolve_output()
        self.suffix = preset.suffix
        self.cache_file = self.output / self.CACHE_FILE
        self.cache = {}
        self.output_files = set()
        self.saved_images = 0
        self.source_sizes = []
        self.saved_sizes = []
        self.saved_qualities = []
        self.stop_event = stop_event or Event()
        self.log = print
        self.progress_callback = progress_callback
        self.completed_results = {}
        self.files_total = 0

    def resolve_output(self):
        output = Path(self.preset.output_folder)
        if not output.is_absolute():
            output = self.source_root.parent / output
        return output.resolve()

    def human_size(self, size):
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        return f"{size / 1024 / 1024 / 1024:.2f} GB"

    def read_cache(self):
        try:
            with open(self.cache_file, "rb") as file:
                return pickle.load(file)
        except Exception:
            return {}

    def write_cache(self):
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_file, "wb") as file:
            pickle.dump(self.cache, file, protocol=pickle.HIGHEST_PROTOCOL)

    def get_output_name(self, file):
        found = re.findall(self.REGEX_NUMBER, file.stem)
        quality = re.findall(self.REGEX_QUALITY, file.stem)
        if found and found[0] and found[0][0] and found[0][1]:
            file_number = int(found[0][0])
            file_subnumber = int(found[0][1])
            converted_name = ""
            relative_folder = file.parent.relative_to(self.source_root).as_posix()
            for folder in relative_folder.split("/"):
                if not folder:
                    continue
                if folder == "00_parts":
                    converted_name += "ps"
                elif converted_name == "ps":
                    numbers = re.findall(r"\d+", folder)
                    if numbers:
                        converted_name += numbers[0]
                else:
                    converted_name += "_"
                    converted_name += "".join(char for char in folder if not char.isdigit())
            converted_name = converted_name.replace("__", "_")
            output_name = f"{converted_name}_{file_number:02d}_{file_subnumber:03d}{self.suffix}.jpg"
        else:
            output_name = f"{file.stem}{self.suffix}.jpg"
        multiplier = int(quality[0]) / 100 if quality else 1.0
        multiplier = max(0.0, min(1.0, multiplier))
        return output_name, multiplier

    def get_output_file(self, file):
        output_name, _ = self.get_output_name(file)
        relative_folder = file.parent.relative_to(self.source_root)
        return self.output / relative_folder / output_name

    def scan(self):
        files = {}
        all_files = []

        for folder, dirs, names in os.walk(self.source):
            if self.stop_event.is_set():
                break

            dirs[:] = [
                directory
                for directory in dirs
                if directory.lower() not in self.IGNORE_FOLDERS
            ]

            names.sort()

            for name in names:
                if self.stop_event.is_set():
                    break

                file = Path(folder) / name

                if (
                    not file.is_file()
                    or file.suffix.lower() not in self.INPUT_SUFFIXES
                    or " - " in file.stem
                ):
                    continue

                all_files.append(file)

                output_name, multiplier = self.get_output_name(file)

                relative_folder = file.parent.relative_to(self.source_root)
                output_file = self.output / relative_folder / output_name

                self.output_files.add(output_file)

                # ВАЖНО: всегда относительно project root
                relative_file = file.relative_to(self.source_root).as_posix()

                mod_time = int(file.stat().st_mtime)

                cache_settings = (
                    mod_time,
                    self.resize_mode,
                    self.resolution_width,
                    self.resolution_height,
                    self.downscale,
                    self.sharpen_radius,
                    self.sharpen_percent,
                    self.sharpen_threshold,
                )

                if (
                    output_file.exists()
                    and self.cache.get(relative_file) == cache_settings
                ):
                    continue

                files[file] = {
                    "output_file": output_file,
                    "quality": multiplier,
                    "relative_file": relative_file,
                    "mod_time": mod_time,
                }

        self.files_total = len(all_files)
        return files, all_files

    def resize_image(self, image):
        if self.resize_mode == "Resolution":
            return image.resize((self.resolution_width, self.resolution_height), self.RESAMPLE)
        width = max(1, round(image.width / self.downscale))
        height = max(1, round(image.height / self.downscale))
        return image.resize((width, height), self.RESAMPLE)

    def convert_file(self, source, data, index):
        if self.stop_event.is_set():
            return
        output = data["output_file"]
        multiplier = data["quality"]
        relative_file = data["relative_file"]
        mod_time = data["mod_time"]
        quality = self.maximum_quality
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(source) as image:
                image = self.resize_image(image)
                icc_profile = image.info.get("icc_profile", self.PROFILE_SRGB)
                exif = image.getexif()
                for tag, value in self.EXIF_DATA.items():
                    exif[tag] = value
                rgb = image.convert("RGB")
                if self.sharpen_percent > 0 and self.sharpen_radius > 0:
                    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=self.sharpen_radius, percent=self.sharpen_percent, threshold=self.sharpen_threshold))
                while True:
                    if self.stop_event.is_set():
                        return
                    buffer = BytesIO()
                    rgb.save(buffer, format="JPEG", quality=quality, icc_profile=icc_profile)
                    file_size = buffer.tell()
                    buffer.close()
                    if file_size <= self.target_size * 1024 or quality <= self.minimum_quality:
                        rgb.save(output, format="JPEG", quality=min(100, round(quality * multiplier)), icc_profile=icc_profile, exif=exif)
                        break
                    quality -= 1
            source_size = source.stat().st_size
            saved_size = output.stat().st_size
            result = {
                "index": index,
                "source": source,
                "output": output,
                "width": rgb.width,
                "height": rgb.height,
                "quality": quality,
                "size": saved_size,
                "source_size": source_size,
                "success": True,
                "error": None,
            }
            self.cache[relative_file] = (mod_time, self.resize_mode, self.resolution_width, self.resolution_height, self.downscale, self.sharpen_radius, self.sharpen_percent, self.sharpen_threshold)
        except Exception as exc:
            result = {"index": index, "source": source, "output": output, "width": 0, "height": 0, "quality": 0, "size": 0, "source_size": 0, "success": False, "error": str(exc)}
        self.completed_results[index] = result
        if self.progress_callback:
            self.progress_callback(1, self.files_total)
        return result

    def run(self):
        start = time()
        self.output.mkdir(parents=True, exist_ok=True)
        self.cache = self.read_cache()
        files, all_files = self.scan()
        if self.progress_callback:
            self.progress_callback(0, self.files_total)
        workers = []
        max_workers = max(2, int((os.cpu_count() or 1) * 0.925))
        indexed_files = list(enumerate(all_files, 1))
        pending_indexes = {index for index, _ in indexed_files}
        for index, source in indexed_files:
            if self.stop_event.is_set():
                break
            if source not in files:
                self.completed_results[index] = {
                    "index": index,
                    "source": source,
                    "output": self.get_output_file(source),
                    "width": 0,
                    "height": 0,
                    "quality": 0,
                    "size": 0,
                    "source_size": 0,
                    "success": True,
                    "cached": True,
                    "error": None,
                }
                if self.progress_callback:
                    self.progress_callback(1, self.files_total)
                continue
            data = files[source]
            while len(workers) >= max_workers:
                for worker in workers[:]:
                    if not worker.is_alive():
                        worker.join()
                        workers.remove(worker)
                if len(workers) >= max_workers:
                    self.stop_event.wait(0.01)
            worker = Thread(target=self.convert_file, args=(source, data, index))
            worker.start()
            workers.append(worker)
        for worker in workers:
            worker.join()
        self.write_cache()
        for index in range(1, len(all_files) + 1):
            if self.stop_event.is_set():
                break
            result = self.completed_results.get(index)
            if not result:
                continue
            if result.get("cached"):
                output = result["output"]
                self.log(f"{index}/{self.files_total}: {output} | cached")
                continue
            if result["success"]:
                self.saved_images += 1
                self.source_sizes.append(result["source_size"])
                self.saved_sizes.append(result["size"])
                self.saved_qualities.append(result["quality"])
                self.log(f"{index}/{self.files_total}: {result['output']} | {result['width']}x{result['height']} | {result['quality']:.0f}% | {self.human_size(result['size'])}")
            else:
                self.log(f"{index}/{self.files_total}: ERROR: {result['source']} | {result['error']}")
        self.log(f"Images converted: {self.saved_images}")
        self.log(f"Finished in {time() - start:.1f} sec")

class WebMConverter:
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
    def __init__(self, folder, preset, local_settings=None, stop_event=None, progress_callback=None):
        self.folder = Path(folder).resolve()
        self.preset = preset
        self.settings = normalize_webm_settings(preset.webm)
        if local_settings:
            self.settings.update(normalize_webm_settings(local_settings))
        self.settings = normalize_webm_settings(self.settings)
        self.stop_event = stop_event or Event()
        self.process = None
        self.log = print
        self.progress_callback = progress_callback
        output = Path(preset.output_folder)
        if not output.is_absolute():
            output = self.folder.parent / output
        self.output_folder = output.resolve()

    def get_images(self):
        images = [path for path in self.folder.iterdir() if path.is_file() and path.suffix.lower() in self.IMAGE_EXTENSIONS]
        def natural_sort(path):
            numbers = re.findall(r"\d+", path.stem)
            return int(numbers[-1]) if numbers else path.stem.lower()
        images.sort(key=natural_sort)
        return images

    def get_output_file(self):
        return self.output_folder / f"{self.folder.name}{self.preset.suffix}.webm"

    def get_cache_file(self):
        return self.folder / WEBM_CACHE_FILE

    def get_size(self):
        output = self.get_output_file()
        if output.exists():
            try:
                return output.stat().st_size
            except OSError:
                pass
        return 0

    def resolution(self):
        if self.settings["resize_mode"] == "Resolution":
            return self.settings["resolution_width"], self.settings["resolution_height"]
        return None

    def read_cache(self):
        try:
            with open(self.get_cache_file(), "r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def write_cache(self, signature):
        try:
            with open(self.get_cache_file(), "w", encoding="utf-8") as file:
                json.dump({"version": 1, "signature": signature}, file, ensure_ascii=False, indent=2, sort_keys=True)
        except OSError as exc:
            self.log(f"WebM cache warning: {exc}")

    def get_source_signature(self, images):
        frames = []
        for image in images:
            try:
                stat = image.stat()
            except OSError:
                continue
            frames.append({"path": image.resolve().as_posix(), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size})
        data = {"version": 2, "frames": frames, "settings": self.settings, "preset": {"suffix": self.preset.suffix, "output_folder": self.preset.output_folder}}
        serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def needs_conversion(self, images):
        output = self.get_output_file()
        if not output.exists():
            return True, "output does not exist"
        cache = self.read_cache()
        signature = self.get_source_signature(images)
        if cache.get("signature") != signature:
            return True, "frames or settings changed"
        try:
            if output.stat().st_size <= 0:
                return True, "output file is empty"
        except OSError:
            return True, "output file cannot be read"
        return False, "nothing changed"

    def create_concat_file(self, images):
        digest = hashlib.sha1(str(self.folder).encode()).hexdigest()
        concat_file = self.folder / f".kconverter_{digest}.txt"
        fps = float(self.settings["input_fps"]) * float(self.settings["speed"])
        duration = 1.0 / fps
        with open(concat_file, "w", encoding="utf-8") as file:
            for image in images:
                path = image.resolve().as_posix().replace("'", "'\\''")
                file.write(f"file '{path}'\n")
                file.write(f"duration {duration:.12f}\n")
            if images:
                path = images[-1].resolve().as_posix().replace("'", "'\\''")
                file.write(f"file '{path}'\n")
        return concat_file

    def build_filters(self):
        settings = self.settings
        input_fps = float(settings["input_fps"])
        output_fps = float(settings["output_fps"])
        speed = float(settings["speed"])
        effective_fps = input_fps * speed
        interpolate = int(settings["interpolate"])
        filters = []
        if effective_fps < output_fps:
            if interpolate == 0:
                filters.append(f"fps={effective_fps:g}")
            elif interpolate in MINTERPOLATE:
                filters.append(MINTERPOLATE[interpolate].format(output_fps))
        else:
            filters.append(f"fps={output_fps:g}")
        resolution = self.resolution()
        if resolution:
            filters.append(f"scale={resolution[0]}:{resolution[1]}:flags=lanczos:param0=4")
        elif settings["resize_mode"] == "Downsample" and float(settings["downsample"]) != 1.0:
            downsample = float(settings["downsample"])
            filters.append(f"scale=iw/{downsample:g}:ih/{downsample:g}:flags=lanczos:param0=4")
        sharpen = float(settings["sharpen"])
        if sharpen:
            filters.append(f"unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount={sharpen}")
        return filters

    def run_process(self, command):
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        while True:
            if self.stop_event.is_set():
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                raise InterruptedError("WebM conversion stopped.")
            line = self.process.stdout.readline()
            if line:
                self.log(line.rstrip())
            if self.process.poll() is not None:
                break
        if self.process.returncode != 0:
            raise RuntimeError(f"FFmpeg exited with code {self.process.returncode}")
        self.process = None

    def run(self):
        images = self.get_images()
        if not images:
            self.log(f"No images found: {self.folder}")
            if self.progress_callback:
                self.progress_callback(1, 1)
            return
        if self.progress_callback:
            self.progress_callback(0, 1)
        self.output_folder.mkdir(parents=True, exist_ok=True)
        should_convert, reason = self.needs_conversion(images)
        if not should_convert:
            self.log(f"WebM skipped: {self.folder.name} (frames and settings unchanged)")
            if self.progress_callback:
                self.progress_callback(1, 1)
            return
        self.log(f"WebM conversion required: {reason}")
        settings = self.settings
        if settings["reverse"]:
            images.reverse()
        concat_file = self.create_concat_file(images)
        output = self.get_output_file()
        filters = self.build_filters()
        input_stream = ffmpeg.input(str(concat_file), format="concat", safe=0)
        params = {
            "c:v": settings["codec"],
            "pix_fmt": settings["pix_fmt"],
            "threads": settings["threads"],
            "row-mt": settings["row_mt"],
            "tile-columns": settings["tile_columns"],
            "tile-rows": settings["tile_rows"],
            "frame-parallel": 1,
            "cpu-used": settings["cpu_used"],
            "crf": settings["crf"],
            "b:v": 0,
            "color_range": "pc",
            "color_primaries": "bt709",
            "color_trc": "bt709",
            "colorspace": "bt709",
            "map_metadata": -1,
            "f": "webm",
            "an": None,
        }
        if filters:
            params["vf"] = ",".join(filters)
        if settings["loop"]:
            params["loop"] = 0
        command = ffmpeg.compile(input_stream.output(str(output), **params), overwrite_output=True)
        self.log(f"WebM: {self.folder.name}")
        self.log(f"Output: {output}")
        self.log(f"Frames: {len(images)}")
        if settings["resize_mode"] == "Resolution":
            self.log(f"Resolution: {settings['resolution_width']}x{settings['resolution_height']}")
        else:
            self.log(f"Downsample: {settings['downsample']}x")
        self.log(f"FPS: {settings['input_fps']} -> {settings['output_fps']}")
        self.log(f"CRF: {settings['crf']}")
        try:
            self.run_process(command)
        finally:
            concat_file.unlink(missing_ok=True)
        if output.exists():
            self.write_cache(self.get_source_signature(images))
            self.log(f"Finished: {output} ({self.format_size(output.stat().st_size)})")
        if self.progress_callback:
            self.progress_callback(1, 1)

    def format_size(self, size):
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        return f"{size / 1024 / 1024 / 1024:.2f} GB"

class ConversionThread(QThread):
    message = pyqtSignal(str)
    error = pyqtSignal(str)
    finished_signal = pyqtSignal(object)
    progress = pyqtSignal(int, int, float)

    def __init__(self, jobs):
        super().__init__()
        self.jobs = jobs
        self.stop_event = Event()
        self.progress_done = 0
        self.progress_total = 0
        self.progress_start = time()
        self.progress_lock = __import__("threading").Lock()

    def stop(self):
        self.stop_event.set()

    def add_progress(self, done, total):
        with self.progress_lock:
            previous_total = self.progress_total
            if total > 0 and previous_total < self.progress_total + total:
                pass
            self.progress_done += done
            elapsed = max(0.001, time() - self.progress_start)
            speed = self.progress_done / elapsed
            remaining = max(0, self.progress_total - self.progress_done)
            eta = remaining / speed if speed > 0 else 0
            current_done = self.progress_done
            current_total = self.progress_total
        self.progress.emit(current_done, current_total, eta)

    def run(self):
        changed_folders = set()

        try:
            total_jobs = len(self.jobs)
            planned_units = []
            for settings, preset, folder, local_settings in self.jobs:
                if settings.mode == "Images":
                    count = 0
                    try:
                        root = Path(folder)
                        for base, dirs, names in os.walk(root):
                            dirs[:] = [d for d in dirs if d.lower() not in ImageConverter.IGNORE_FOLDERS]
                            count += sum(1 for name in names if Path(name).suffix.lower() in ImageConverter.INPUT_SUFFIXES and " - " not in Path(name).stem)
                    except OSError:
                        count = 0
                    planned_units.append(max(1, count))
                else:
                    planned_units.append(1)
            self.progress_total = sum(planned_units)
            self.progress_done = 0
            self.progress_start = time()
            self.progress.emit(0, max(1, self.progress_total), 0)

            for index, job in enumerate(
                self.jobs,
                1,
            ):
                if self.stop_event.is_set():
                    break

                settings, preset, folder, local_settings = job

                changed_folders.add(
                    str(
                        Path(folder).resolve()
                    )
                )

                self.message.emit(f"[{index}/{total_jobs}] {settings.mode} | {preset.name} | {folder}")
                if settings.mode == "Images":
                    converter = ImageConverter(
                        folder,
                        preset,
                        self.stop_event,
                        self.add_progress,
                        source_root=settings.source_folder,
                    )
                else:
                    converter = WebMConverter(
                        folder,
                        preset,
                        local_settings,
                        self.stop_event,
                        self.add_progress,
                    )
                converter.log = self.message.emit
                try:
                    converter.run()
                except InterruptedError:
                    self.message.emit("Conversion stopped.")
                    break
            if self.stop_event.is_set():
                self.message.emit("Conversion stopped.")
            else:
                self.progress.emit(max(1, self.progress_total), max(1, self.progress_total), 0)
                self.message.emit("All conversions completed.")
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished_signal.emit(
                changed_folders
            )

class MainWindow(QMainWindow):
    PROJECT_VERSION = 16
    def __init__(self):
        super().__init__()
        self.folders = []
        self.thread = None
        self.thumbnail_worker = None
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

        self._watcher.directoryChanged.connect(
            self._on_watched_directory_changed
        )

        self._watcher.fileChanged.connect(
            self._on_watched_file_changed
        )
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
        return (
            str(Path(folder).resolve()),
            self._preset_cache_key(preset),
        )

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

    def populate_tree(
        self,
        parent,
        folder,
        settings,
    ):
        self._populate_folder_children(
            parent,
            Path(folder).resolve(),
            settings,
        )


    def _populate_folder_children(
        self,
        parent,
        folder,
        settings,
    ):
        folder = Path(folder).resolve()

        try:
            entries = sorted(
                folder.iterdir(),
                key=lambda path: (
                    not path.is_dir(),
                    path.name.lower(),
                ),
            )
        except (
            PermissionError,
            OSError,
        ):
            return

        for entry in entries:
            if (
                entry.is_dir()
                and entry.name.lower() == "backup"
            ):
                continue

            child = QTreeWidgetItem(
                [entry.name]
            )

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
                QSize(
                    0,
                    FOLDER_ROW_HEIGHT,
                ),
            )

            parent.addChild(child)

            resolved = str(
                entry.resolve()
            )

            self.items_by_path[
                resolved
            ] = child

            if entry.is_dir():
                self.tree.setItemWidget(
                    child,
                    1,
                    self.create_folder_status(
                        settings,
                        entry,
                        False,
                    ),
                )

                self._populate_folder_children(
                    child,
                    entry,
                    settings,
                )

            elif (
                entry.suffix.lower()
                in WebMConverter.IMAGE_EXTENSIONS
            ):
                if (
                    settings.mode == "Images"
                    and entry.suffix.lower() == ".png"
                ):
                    self.tree.setItemWidget(
                        child,
                        1,
                        self.create_file_status(
                            settings,
                            entry,
                        ),
                    )

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

        for index, preset in enumerate(settings.presets):
            # if index:
            #     layout.addWidget(self.create_separator())

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

    def folder_has_outdated_images(
        self,
        settings,
        preset,
        folder,
    ):
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

        prefix = str(folder)

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

    def get_file_status(
        self,
        source,
        preset,
    ):
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

        key = (
            str(source),
            self._preset_cache_key(preset),
        )

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

        cached = self._file_status_cache.get(
            key
        )

        if (
            cached is not None
            and cached["signature"] == signature
        ):
            return (
                output,
                cached["status"],
            )

        if output_stat is None:
            status = "—"

        elif (
            output_stat.st_mtime_ns
            < source_stat.st_mtime_ns
        ):
            status = "Outdated"

        else:
            status = self.format_size(
                output_stat.st_size
            )

        self._file_status_cache[key] = {
            "signature": signature,
            "status": status,
        }

        return output, status

    def _invalidate_file_status_cache(
        self,
        folder,
    ):
        folder = Path(folder).resolve()

        for key in list(
            self._file_status_cache
        ):
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
        if self.thumbnail_worker:
            self.thumbnail_worker.stop()
            self.thumbnail_worker.wait()
        self.thumbnail_worker = ThumbnailWorker(paths)
        self.thumbnail_worker.thumbnail_ready.connect(self.thumbnail_ready)
        self.thumbnail_worker.finished.connect(self.thumbnail_finished)
        self.thumbnail_worker.start()

    def _start_folder_thumbnail_worker(
        self,
        folder,
    ):
        folder = Path(folder).resolve()

        paths = []

        try:
            for path in folder.iterdir():
                if (
                    path.is_file()
                    and path.suffix.lower()
                    in WebMConverter.IMAGE_EXTENSIONS
                ):
                    paths.append(
                        (
                            str(path),
                            path,
                        )
                    )

        except OSError:
            return

        item = self.items_by_path.get(
            str(folder)
        )

        if item is not None:
            thumbnail_source = (
                self.get_folder_thumbnail_source(
                    folder
                )
            )

            if thumbnail_source:
                paths.append(
                    (
                        str(folder),
                        thumbnail_source,
                    )
                )

        if not paths:
            return

        if self.thumbnail_worker:
            self.thumbnail_worker.stop()
            self.thumbnail_worker.wait()
            self.thumbnail_worker = None

        self.thumbnail_worker = ThumbnailWorker(
            paths
        )

        self.thumbnail_worker.thumbnail_ready.connect(
            self.thumbnail_ready
        )

        self.thumbnail_worker.finished.connect(
            self.thumbnail_finished
        )

        self.thumbnail_worker.start()

    def thumbnail_ready(self, path, pixmap):
        item = self.items_by_path.get(str(Path(path).resolve()))
        if item is not None:
            item.setIcon(0, QIcon(pixmap))

    def thumbnail_finished(self):
        self.thumbnail_worker = None

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
        if self._rescanning:
            return

        self._rescanning = True

        try:
            state = self._tree_state_to_restore

            if state is None:
                expanded, vertical_scroll, horizontal_scroll = (
                    self.get_tree_state()
                )
            else:
                expanded = set(
                    state.get("expanded", set())
                )
                vertical_scroll = int(
                    state.get("vertical_scroll", 0)
                )
                horizontal_scroll = int(
                    state.get("horizontal_scroll", 0)
                )

                self._tree_state_to_restore = None

            if changed_paths is None:
                changed_paths = set()

            changed_paths = {
                str(Path(path).resolve())
                for path in changed_paths
            }

            # --------------------------------------------
            # Первый запуск / загрузка проекта
            # --------------------------------------------

            if (
                not self.items_by_path
                or self.tree.topLevelItemCount() == 0
            ):
                self._full_rescan()

            # --------------------------------------------
            # Incremental rescan
            # --------------------------------------------

            elif changed_paths:
                self._incremental_rescan(
                    changed_paths
                )

            else:
                # Нажатие кнопки Rescan вручную.
                #
                # Не очищаем дерево.
                # Просто проверяем watcher snapshots.
                self._manual_change_check()

            self.restore_tree_state(
                expanded,
                vertical_scroll,
                horizontal_scroll,
            )

            self._register_watch_directories()

        finally:
            self._rescanning = False

        self.log_message(
            "Folders rescanned incrementally."
        )

    def _manual_change_check(self):
        changed = set()

        for directory, old_signature in list(
            self._directory_snapshot.items()
        ):
            new_signature = (
                self._directory_signature(
                    directory
                )
            )

            if new_signature != old_signature:
                changed.add(directory)

                if new_signature is None:
                    self._directory_snapshot.pop(
                        directory,
                        None,
                    )
                else:
                    self._directory_snapshot[
                        directory
                    ] = new_signature

        if changed:
            self._incremental_rescan(
                changed
            )
        else:
            self.log_message(
                "Rescan: no filesystem changes."
            )

    def _full_rescan(self):
        if self.thumbnail_worker:
            self.thumbnail_worker.stop()
            self.thumbnail_worker.wait()
            self.thumbnail_worker = None

        self.items_by_path.clear()
        self.settings_by_item.clear()

        self._folder_status_cache.clear()
        self._directory_snapshot.clear()

        self.tree.clear()

        for settings in self.folders:
            self.add_folder_item(settings)

        self.start_thumbnail_worker()

        self._register_watch_directories()

    def _incremental_rescan(self, changed_paths):
        affected = set()

        for changed in changed_paths:
            path = Path(changed).resolve()

            settings = self.find_settings_for_path(path)

            if settings is None:
                continue

            root = Path(
                settings.source_folder
            ).resolve()

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

        for path in sorted(
            affected,
            key=lambda p: len(p.parts),
        ):
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

        item = self.items_by_path.get(
            str(folder)
        )

        # Папка была удалена.
        if not folder.exists():
            if item is not None:
                parent = item.parent()

                path = str(folder)

                self._remove_tree_item(
                    item
                )

                if parent is not None:
                    self._invalidate_folder_cache(
                        Path(
                            parent.data(
                                0,
                                Qt.ItemDataRole.UserRole,
                            )
                        )
                    )

            return

        if not folder.is_dir():
            return

        # Новая директория.
        if item is None:
            parent_folder = folder.parent

            parent_item = self.items_by_path.get(
                str(parent_folder)
            )

            if parent_item is not None:
                self._refresh_tree_folder(
                    parent_folder
                )

            return

        expanded = item.isExpanded()

        self._invalidate_file_status_cache(folder)
        self._invalidate_cache_for_tree(folder)

        # Удаляем только children этой папки.
        for index in range(item.childCount() - 1, -1, -1):
            child = item.child(index)

            self._remove_tree_item(
                child,
                detach=True,
            )

        item.takeChildren()

        # Создаём только содержимое изменившейся папки.
        self._populate_folder_children(
            item,
            folder,
            settings,
        )

        item.setExpanded(expanded)

        # Thumbnail только для этой папки.
        self._start_folder_thumbnail_worker(
            folder
        )

    def _remove_tree_item(
        self,
        item,
        detach=False,
    ):
        for index in range(item.childCount() - 1, -1, -1):
            child = item.child(index)

            self._remove_tree_item(
                child,
                detach=True,
            )

        path = item.data(
            0,
            Qt.ItemDataRole.UserRole,
        )

        if path:
            path = str(
                Path(path).resolve()
            )

            self.items_by_path.pop(
                path,
                None,
            )

        self.settings_by_item.pop(
            id(item),
            None,
        )

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

    def conversion_finished(
        self,
        changed_folders,
    ):
        if self.thread:
            self.thread.deleteLater()

        self.thread = None

        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setFormat("Done")

        self.convert_all_button.setEnabled(
            True
        )

        self.rescan_button.setEnabled(
            True
        )

        self.stop_button.setEnabled(
            False
        )

        if changed_folders:
            self.rescan(
                changed_folders
            )

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
            QMessageBox.warning(self, "Conversion running", "Stop the conversion before closing.")
            event.ignore()
            return
        if self.thumbnail_worker:
            self.thumbnail_worker.stop()
            self.thumbnail_worker.wait()
        if self.confirm_save():
            event.accept()
        else:
            event.ignore()

if __name__ == "__main__":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APPID)
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
