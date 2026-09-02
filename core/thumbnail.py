################################################################################
## Thumbnail Worker

import hashlib

from io import BytesIO
from threading import Event
from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import  QIcon, QPixmap

from config import THUMBNAIL_SIZE, THUMBNAIL_DIR


class ThumbnailWorker(QThread):
    """
    Create thumbnails.
    """

    thumbnail_ready = pyqtSignal(str, object)


    def __init__(self, items):
        super().__init__()

        self.items = items
        self.stop_event = Event()


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
            with Image.open(path, mode='r') as image:
                image.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.BICUBIC)

                buffer = BytesIO()
                image.save(buffer, "PNG", optimize=True)

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


    def stop(self):
        self.stop_event.set()
