################################################################################
## Local Image Settings

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QMessageBox,
    QDialogButtonBox,
    QWidget,
    QDialog,
)

from .preset import PresetDialog


class LocalImageDialog(PresetDialog):

    def __init__(
        self,
        folder,
        preset,
        parent=None,
        enabled_overrides=None,
    ):
        enabled_overrides = set(enabled_overrides or [])

        super().__init__(
            preset,
            "Images",
            parent,
            local_override=True,
            enabled_overrides=enabled_overrides,
        )

        self.folder = folder
        self.deleted = False

        self.local_overrides = {}

        self.setWindowTitle(f"Local Image Settings - {folder.name}")
        self.resize(500, 0)

        # Local settings belong to an existing global preset.
        self.name_edit.setEnabled(False)

        self._replace_buttons()

    def _replace_buttons(self):

        old_button_box = self.findChild(QDialogButtonBox)

        if old_button_box is not None:
            self.layout().removeWidget(old_button_box)
            old_button_box.setParent(None)
            old_button_box.deleteLater()

        buttons_layout = QHBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)

        save_button = QPushButton("Save")

        delete_button = QPushButton("Delete")
        delete_button.setStyleSheet(
            "QPushButton { color: #f00; }"
        )
        delete_button.setToolTip(
            "Remove local settings and use the global preset."
        )

        cancel_button = QPushButton("Cancel")

        buttons_layout.addStretch()
        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(delete_button)
        buttons_layout.addWidget(cancel_button)

        container = QWidget()
        container.setLayout(buttons_layout)

        self.layout().addWidget(container)

        save_button.clicked.connect(self._save)
        delete_button.clicked.connect(self._delete)
        cancel_button.clicked.connect(self.reject)

    def _save(self):

        self.deleted = False

        self.local_overrides = self.get_local_overrides()

        super().accept()

    def _delete(self):

        result = QMessageBox.question(
            self,
            "Delete Local Settings",
            f'Delete local settings for preset "{self.preset.name}" in:\n'
            f"{self.folder}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if result != QMessageBox.StandardButton.Yes:
            return

        self.deleted = True
        self.local_overrides = {}

        # Do NOT call self.accept() here.
        QDialog.accept(self)


