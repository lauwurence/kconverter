from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from core.conversion.audio import SUPPORTED_BITRATES

SETTINGS_FILENAME = ".kconverter_audio_local.json"


def _settings_path(folder: Path) -> Path:
    return Path(folder).resolve() / SETTINGS_FILENAME


def read_local_audio_settings(folder):
    path = _settings_path(Path(folder))
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_local_audio_settings(folder, data):
    path = _settings_path(Path(folder))
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


class LocalAudioDialog(QDialog):
    def __init__(self, folder, preset_name, bitrate=128, parent=None):
        super().__init__(parent)
        self.folder = Path(folder).resolve()
        self.preset_name = preset_name
        self.deleted = False
        self.setWindowTitle(f"Local Audio settings — {preset_name}")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Folder: {self.folder}"))
        layout.addWidget(QLabel("Bitrate:"))

        self.bitrate_combo = QComboBox()
        for value in SUPPORTED_BITRATES:
            self.bitrate_combo.addItem(f"{value} kbps", value)
        index = self.bitrate_combo.findData(int(bitrate))
        self.bitrate_combo.setCurrentIndex(index if index >= 0 else self.bitrate_combo.findData(128))
        layout.addWidget(self.bitrate_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        reset = buttons.addButton("Remove local override", QDialogButtonBox.ButtonRole.DestructiveRole)
        reset.clicked.connect(self.remove_override)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def bitrate(self):
        return int(self.bitrate_combo.currentData())

    def remove_override(self):
        self.deleted = True
        self.accept()
