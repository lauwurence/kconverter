################################################################################
## WebM

import re

from pathlib import Path
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout,
    QDialog, QDialogButtonBox, QFormLayout,
    QMessageBox, QSpinBox, QDoubleSpinBox, QLabel,
    QCheckBox, QLineEdit, QPushButton, QFileDialog
)
from config import LOCAL_WEBM_FILE, ICON

from .resize import ResizeControls


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


class LocalWebMDialog(QDialog):

    def __init__(self, folder, preset, settings, parent=None):
        super().__init__(parent)
        self.folder = Path(folder).resolve()
        self.preset = preset
        self.settings = normalize_webm_settings(settings)
        self.deleted = False
        self.setWindowTitle(f"Local WebM Settings - {preset.name}")
        self.setWindowIcon(QIcon(ICON))
        self.resize(500, 0)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.add_controls(form)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)

        self.delete_button = buttons.addButton("Delete", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.delete_button.setStyleSheet("QPushButton { color: #f00; } QPushButton:disabled { color: #800; } ")
        self.delete_button.setEnabled((self.folder / LOCAL_WEBM_FILE).exists())
        self.delete_button.clicked.connect(self.delete_local)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_output(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose output folder"
        )

        if folder:
            self.output_folder.setText(
                str(Path(folder).resolve())
            )

    def add_controls(self, form):
        s = self.settings

        output_layout = QHBoxLayout()
        self.output_folder = QLineEdit()
        self.output_folder.setText(s.get("output_folder", self.preset.output_folder))
        output_button = QPushButton("Choose")
        output_button.clicked.connect(self.choose_output)
        output_layout.addWidget(self.output_folder)
        output_layout.addWidget(output_button)
        form.addRow("Output folder:", output_layout)

        self.suffix = QLineEdit()
        self.suffix.setText(s.get("suffix", self.preset.suffix))
        self.suffix.setPlaceholderText("Example: @2")
        form.addRow("File suffix:", self.suffix)

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
        form.addRow("Image quality:", self.image_quality)

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
            "suffix": self.suffix.text(),
            "output_folder": self.output_folder.text().strip(),
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
        result = QMessageBox.question(self, "Delete local settings", f'Delete local WebM settings for "{self.preset.name}" in:\n{self.folder}?')
        if result == QMessageBox.StandardButton.Yes:
            self.deleted = True
            self.accept()

    def accept(self):

        if not self.deleted:
            self.settings = self.collect()

        super().accept()
