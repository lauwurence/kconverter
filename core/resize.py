from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
)

from PyQt6.QtWidgets import QSizePolicy

class ResizeControls:

    def __init__(
        self,
        form,
        resize_mode="Downsample",
        width=1920,
        height=1080,
        downsample=1.0,
        local_override=False,
        enabled_overrides=None,
    ):
        self.local_override = local_override
        self.enabled_overrides = set(enabled_overrides or [])

        self.resize_mode = QComboBox()
        self.resize_mode.addItems(["Resolution", "Downsample"])
        self.resize_mode.setCurrentText(resize_mode)
        self.resize_mode.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.resolution_width = QSpinBox()
        self.resolution_width.setRange(1, 999999)
        self.resolution_width.setValue(max(1, width))
        self.resolution_width.setSuffix(" px")
        self.resolution_width.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.resolution_height = QSpinBox()
        self.resolution_height.setRange(1, 999999)
        self.resolution_height.setValue(max(1, height))
        self.resolution_height.setSuffix(" px")
        self.resolution_height.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.downsample = QDoubleSpinBox()
        self.downsample.setRange(0.01, 100.0)
        self.downsample.setDecimals(2)
        self.downsample.setSingleStep(0.1)
        self.downsample.setValue(max(0.01, downsample))
        self.downsample.setSuffix("×")

        # ------------------------------------------------------------------
        # Common layout — одинаковый для Normal и Local
        # ------------------------------------------------------------------

        # Width + Height
        self.resolution_widget = QWidget()
        resolution_layout = QHBoxLayout(self.resolution_widget)
        resolution_layout.setContentsMargins(0, 0, 0, 0)
        resolution_layout.setSpacing(4)

        resolution_layout.addWidget(self.resolution_width)

        resolution_layout.addWidget(self.resolution_height)

        if local_override:
            self.resolution_check = QCheckBox()
            self.resolution_check.setChecked("resolution_height" in self.enabled_overrides or "resolution_width" in self.enabled_overrides)
            resolution_layout.addWidget(self.resolution_check)

        # Downsample
        self.downsample_widget = QWidget()
        downsample_layout = QHBoxLayout(self.downsample_widget)
        downsample_layout.setContentsMargins(0, 0, 0, 0)
        downsample_layout.setSpacing(4)

        downsample_layout.addWidget(self.downsample)

        if local_override:
            self.downsample_check = QCheckBox()
            self.downsample_check.setChecked(
                "downsample" in self.enabled_overrides
            )
            downsample_layout.addWidget(self.downsample_check)

        # Resize mode
        if local_override:
            self.resize_mode_check = QCheckBox()
            self.resize_mode_check.setChecked(
                "resize_mode" in self.enabled_overrides
            )

            resize_mode_widget = QWidget()
            resize_mode_layout = QHBoxLayout(resize_mode_widget)
            resize_mode_layout.setContentsMargins(0, 0, 0, 0)
            resize_mode_layout.setSpacing(4)
            resize_mode_layout.addWidget(self.resize_mode)
            resize_mode_layout.addWidget(self.resize_mode_check)

            form.addRow("Resize:", resize_mode_widget)

        else:
            form.addRow("Resize:", self.resize_mode)

        # ------------------------------------------------------------------
        # Same structure as Normal
        # ------------------------------------------------------------------

        self.container = QWidget()
        container_layout = QHBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(4)

        container_layout.addWidget(self.resolution_widget)
        container_layout.addWidget(self.downsample_widget)

        form.addRow("", self.container)

        # ------------------------------------------------------------------
        # Visibility
        # ------------------------------------------------------------------

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

    def get_local_overrides(self):
        if not self.local_override:
            return {}

        values = self.values()
        result = {}

        if self.resize_mode_check.isChecked():
            result["resize_mode"] = values["resize_mode"]

        if self.resolution_check.isChecked():
            result["resolution_width"] = values["resolution_width"]

        if self.resolution_check.isChecked():
            result["resolution_height"] = values["resolution_height"]

        if self.downsample_check.isChecked():
            result["downsample"] = values["downsample"]

        return result

    def get_enabled_overrides(self):
        if not self.local_override:
            return set()

        result = set()

        if self.resize_mode_check.isChecked():
            result.add("resize_mode")

        if self.resolution_check.isChecked():
            result.update({
                "resolution_width",
                "resolution_height",
            })

        if self.downsample_check.isChecked():
            result.add("downsample")

        return result
