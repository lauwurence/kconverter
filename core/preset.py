################################################################################
## Preset

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog,
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
    QMessageBox, QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox
)
from PyQt6.QtWidgets import QSizePolicy

from config import ICON
from .resize import ResizeControls

from .local_webm import normalize_webm_settings

from random import randint


class Preset():

    def __init__(self, name="New Preset", downscale=1.0, target_size=400, max_quality=95, min_quality=50, output_folder="", suffix="", sharpen_radius=0.5, sharpen_percent=0, sharpen_threshold=0, webm=None, resize_mode="Downsample", resolution_width=0, resolution_height=0, id=None, panorama=False, webp=False, webp_method=4, enabled_overrides=None, bitrate=128):
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
        self.id = id or randint(0, 999999999)
        self.panorama = panorama
        self.webp = webp
        self.webp_method = webp_method
        self.bitrate = int(bitrate)
        self.enabled_overrides = set(enabled_overrides or [])

    @property
    def cache_key(self):
        return (
            self.name,
            self.output_folder,
            self.suffix,
            self.resize_mode,
            self.resolution_width,
            self.resolution_height,
            self.downscale,
            self.target_size,
            self.max_quality,
            self.min_quality,
            self.sharpen_radius,
            self.sharpen_percent,
            self.sharpen_threshold,
            self.panorama,
            self.webp,
            self.webp_method,
            self.bitrate,
        )

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
            "id": self.id,
            "panorama": self.panorama,
            "webp": self.webp,
            "webp_method": self.webp_method,
            "bitrate": self.bitrate,
            "enabled_overrides": sorted(self.enabled_overrides),
        }

    @classmethod
    def from_dict(cls, data):
        resize_mode = data.get("resize_mode")

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
            int(data.get("sharpen_threshold", 0)),
            data.get("webm", {}),
            resize_mode,
            int(data.get("resolution_width", 0)),
            int(data.get("resolution_height", 0)),
            data.get("id", None),
            data.get("panorama", False),
            data.get("webp", False),
            data.get("webp_method", 4),
            enabled_overrides=data.get("enabled_overrides", []),
            bitrate=int(data.get("bitrate", 128)),
        )

class PresetDialog(QDialog):

    def __init__(
        self,
        preset,
        mode,
        parent=None,
        local_override=False,
        enabled_overrides=None,
    ):
        super().__init__(parent)

        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)

        # Не перерисовывать окно во время построения интерфейса.
        self.setUpdatesEnabled(False)

        self.preset = preset
        self.mode = mode
        self.local_override = local_override
        self.enabled_overrides = set(
            enabled_overrides
            if enabled_overrides is not None
            else preset.enabled_overrides
        )
        self._local_checks = {}

        self.setWindowTitle("Preset Settings")
        self.setWindowIcon(QIcon(ICON))
        self.resize(500, 0)

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

        if self.local_override:
            self.output_check = QCheckBox()
            self.output_check.setChecked("output_folder" in self.enabled_overrides)
            self.output_check.setToolTip(
                "Override the global Output folder for this folder."
            )
            output_layout.addWidget(self.output_check)
            self._local_checks["output_folder"] = self.output_check

        form.addRow("Output folder:", output_layout)

        suffix_layout = QHBoxLayout()

        self.suffix_edit = QLineEdit(preset.suffix)
        suffix_layout.addWidget(self.suffix_edit)

        if self.local_override:
            self.suffix_check = QCheckBox()
            self.suffix_check.setChecked("suffix" in self.enabled_overrides)
            self.suffix_check.setToolTip(
                "Override the global File suffix for this folder."
            )
            suffix_layout.addWidget(self.suffix_check)
            self._local_checks["suffix"] = self.suffix_check

        form.addRow("File suffix:", suffix_layout)

        if mode == "Images":
            self.add_image_settings(form)
        elif mode == "Audio":
            self.add_audio_settings(form)
        else:
            self.add_webm_settings(form)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

        # self.adjustSize()
        self.setUpdatesEnabled(True)

    def _add_local_field(self, form, label, widget, key):
        if not self.local_override:
            form.addRow(label, widget)
            return

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        row.addWidget(widget)
        widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed
        )

        check = QCheckBox()
        check.setChecked(key in self.enabled_overrides)

        check.setToolTip(
            f"Override the global '{label}' setting for this folder."
        )

        row.addWidget(check)

        self._local_checks[key] = check

        form.addRow(label, row)

    def _add_local_resize_field(self, form, label, widget, key):
        """
        Same as _add_local_field(), kept separate so ResizeControls
        can later expose its individual controls if needed.
        """

        self._add_local_field(form, label, widget, key)

    def get_local_overrides(self):
        result = {}

        if not self.local_override:
            return result

        if self.output_check.isChecked():
            result["output_folder"] = self.output_edit.text().strip()

        if self.suffix_check.isChecked():
            result["suffix"] = self.suffix_edit.text()

        if self._local_checks['target_size'].isChecked():
            result["target_size"] = self.target_size.value()

        if self._local_checks['max_quality'].isChecked():
            result["max_quality"] = self.max_quality.value()

        if self._local_checks['min_quality'].isChecked():
            result["min_quality"] = self.min_quality.value()

        if self._local_checks['sharpen_radius'].isChecked():
            result["sharpen_radius"] = self.sharpen_radius.value()

        if self._local_checks['sharpen_percent'].isChecked():
            result["sharpen_percent"] = self.sharpen_percent.value()

        if self._local_checks['sharpen_threshold'].isChecked():
            result["sharpen_threshold"] = self.sharpen_threshold.value()

        if self.panorama_check.isChecked():
            result["panorama"] = self.panorama_enabled.isChecked()

        if self.webp_check.isChecked():
            result["webp"] = self.webp_enabled.isChecked()

        if self._local_checks['webp_method'].isChecked():
            result["webp_method"] = self.webp_method.value()

        if "bitrate" in self._local_checks and self._local_checks["bitrate"].isChecked():
            result["bitrate"] = int(self.bitrate.currentData())

        if hasattr(self, "resize_controls"):
            result.update(self.resize_controls.get_local_overrides())

        return result

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if folder:
            self.output_edit.setText(folder)

    def add_image_settings(self, form):

        self.resize_controls = ResizeControls(
            form,
            self.preset.resize_mode,
            self.preset.resolution_width or 1920,
            self.preset.resolution_height or 1080,
            self.preset.downscale,
            local_override=self.local_override,
            enabled_overrides=self.enabled_overrides,
        )

        self.target_size = QSpinBox()
        self.target_size.setRange(1, 999999)
        self.target_size.setSuffix(" KB")
        self.target_size.setSingleStep(50)
        self.target_size.setValue(self.preset.target_size)

        self._add_local_field(
            form,
            "Target size:",
            self.target_size,
            "target_size",
        )

        self.max_quality = QSpinBox()
        self.max_quality.setRange(1, 100)
        self.max_quality.setSingleStep(5)
        self.max_quality.setValue(self.preset.max_quality)

        self._add_local_field(
            form,
            "Max quality:",
            self.max_quality,
            "max_quality",
        )

        self.min_quality = QSpinBox()
        self.min_quality.setRange(1, 100)
        self.min_quality.setSingleStep(5)
        self.min_quality.setValue(self.preset.min_quality)

        self._add_local_field(
            form,
            "Min quality:",
            self.min_quality,
            "min_quality",
        )

        self.sharpen_radius = QDoubleSpinBox()
        self.sharpen_radius.setRange(0, 10)
        self.sharpen_radius.setDecimals(2)
        self.sharpen_radius.setSingleStep(0.25)
        self.sharpen_radius.setSuffix(" px")
        self.sharpen_radius.setValue(self.preset.sharpen_radius)

        self._add_local_field(
            form,
            "Sharpen radius:",
            self.sharpen_radius,
            "sharpen_radius",
        )

        self.sharpen_percent = QSpinBox()
        self.sharpen_percent.setRange(0, 1000)
        self.sharpen_percent.setSingleStep(10)
        self.sharpen_percent.setValue(self.preset.sharpen_percent)

        self._add_local_field(
            form,
            "Sharpen percent:",
            self.sharpen_percent,
            "sharpen_percent",
        )

        self.sharpen_threshold = QSpinBox()
        self.sharpen_threshold.setRange(0, 255)
        self.sharpen_threshold.setSingleStep(1)
        self.sharpen_threshold.setValue(self.preset.sharpen_threshold)

        self._add_local_field(
            form,
            "Sharpen threshold:",
            self.sharpen_threshold,
            "sharpen_threshold",
        )

        self.panorama_enabled = QCheckBox()
        self.panorama_enabled.setChecked(self.preset.panorama)

        if self.local_override:
            panorama_layout = QHBoxLayout()
            panorama_layout.setContentsMargins(0, 0, 0, 0)

            panorama_layout.addWidget(self.panorama_enabled)

            self.panorama_check = QCheckBox()
            self.panorama_check.setChecked("panorama" in self.enabled_overrides)
            self.panorama_check.setToolTip(
                "Override the global 'Panorama' setting for this folder."
            )

            panorama_layout.addWidget(self.panorama_check)

            self._local_checks["panorama"] = self.panorama_check

            form.addRow("Panorama:", panorama_layout)
        else:
            form.addRow("Panorama:", self.panorama_enabled)

        self.webp_enabled = QCheckBox()
        self.webp_enabled.setChecked(self.preset.webp)

        if self.local_override:
            webp_layout = QHBoxLayout()
            webp_layout.setContentsMargins(0, 0, 0, 0)

            webp_layout.addWidget(self.webp_enabled)

            self.webp_check = QCheckBox()
            self.webp_check.setChecked("webp" in self.enabled_overrides)
            self.webp_check.setToolTip(
                "Override the global 'WebP' setting for this folder."
            )

            webp_layout.addWidget(self.webp_check)

            self._local_checks["webp"] = self.webp_check

            form.addRow("WebP:", webp_layout)
        else:
            form.addRow("WebP:", self.webp_enabled)

        self.webp_method = QSpinBox()
        self.webp_method.setRange(1, 6)
        self.webp_method.setSingleStep(1)
        self.webp_method.setValue(self.preset.webp_method)

        self._add_local_field(
            form,
            "WebP Method:",
            self.webp_method,
            "webp_method",
        )

    def add_audio_settings(self, form):
        from .conversion.audio import SUPPORTED_BITRATES

        self.bitrate = QComboBox()
        for value in SUPPORTED_BITRATES:
            self.bitrate.addItem(f"{value} kbps", value)

        index = self.bitrate.findData(int(getattr(self.preset, "bitrate", 128)))
        self.bitrate.setCurrentIndex(index if index >= 0 else self.bitrate.findData(128))

        self._add_local_field(
            form,
            "Bitrate:",
            self.bitrate,
            "bitrate",
        )

    def add_webm_settings(self, form):
        settings = normalize_webm_settings(self.preset.webm)

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

        self.resize_controls = ResizeControls(
            form,
            settings["resize_mode"],
            settings["resolution_width"],
            settings["resolution_height"],
            settings["downsample"])

        self.pix_fmt = QComboBox()
        for value in ["yuv420p", "gbrp"]:
            self.pix_fmt.addItem(value, value)
        self.pix_fmt.setCurrentIndex(self.pix_fmt.findData(settings["pix_fmt"]))
        form.addRow("Pixel format:", self.pix_fmt)

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
        form.addRow("Image quality:", self.image_quality)

        self.sharpen = QSpinBox()
        self.sharpen.setRange(0, 1000)
        self.sharpen.setSingleStep(10)
        self.sharpen.setValue(int(settings["sharpen"]))
        form.addRow("Sharpen:", self.sharpen)

        self.sharpen_radius = QDoubleSpinBox()
        self.sharpen_radius.setSuffix(" px")
        self.sharpen_radius.setRange(0, 10)
        self.sharpen_radius.setSingleStep(0.25)
        self.sharpen_radius.setDecimals(2)
        self.sharpen_radius.setValue(settings["sharpen_radius"])
        form.addRow("Sharpen radius:", self.sharpen_radius)

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
            "sharpen_radius": self.sharpen_radius.value(),
            "interpolate": self.interpolate.value(),
            "loop": self.loop.isChecked(),
            "reverse": self.reverse.isChecked(),
            "pix_fmt": self.pix_fmt.currentData(),
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

        if self.local_override:
            self.enabled_overrides = {
                key
                for key, check in self._local_checks.items()
                if check.isChecked()
            }

            self.enabled_overrides.update(
                self.resize_controls.get_enabled_overrides()
            )

            self.preset.enabled_overrides = self.enabled_overrides

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
            self.preset.panorama = self.panorama_enabled.isChecked()
            self.preset.webp = self.webp_enabled.isChecked()
            self.preset.webp_method = self.webp_method.value()

            if self.preset.min_quality > self.preset.max_quality:
                QMessageBox.warning(self, "Invalid quality", "Min quality cannot be greater than Max quality.")
                return

        elif self.mode == "Audio":
            self.preset.bitrate = int(self.bitrate.currentData())

        else:
            self.preset.webm = self.collect_webm()

        super().accept()
