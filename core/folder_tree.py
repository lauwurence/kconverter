################################################################################
## Folder Tree

from pathlib import Path
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtWidgets import QTreeWidget, QHeaderView

from config import THUMBNAIL_SIZE


class FolderTree(QTreeWidget):

    folders_dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()

        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)
        self.setColumnCount(2)
        self.setHeaderLabels(["Folder / File", "Status"])
        self.setIconSize(QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(0, 600)
        # self.setColumnWidth(1, 700)


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
