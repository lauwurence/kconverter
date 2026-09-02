################################################################################
## Root Folder

from pathlib import Path
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QDialog,
    QDialogButtonBox, QFormLayout, QComboBox, QMessageBox,
    QListWidget, QListWidgetItem, QLabel
)

from config import ICON
from .preset import Preset, PresetDialog
from .webm import default_webm_settings, normalize_webm_settings


class FolderSettings():

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
        add_button.clicked.connect(self.add_preset)
        buttons_layout.addWidget(add_button)

        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(self.remove_preset)
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
