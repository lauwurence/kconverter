################################################################################
## Preset

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog,
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
    QMessageBox, QSpinBox, QDoubleSpinBox, QCheckBox
)

from config import ICON
from .resize import ResizeControls

from .webm import normalize_webm_settings


class Preset():

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
