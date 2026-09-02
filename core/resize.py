
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QComboBox,QSpinBox, QDoubleSpinBox


class ResizeControls():

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
