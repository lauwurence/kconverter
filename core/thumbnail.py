################################################################################
## Thumbnail Worker

import hashlib

from pathlib import Path
from threading import Event, Lock

from PIL import Image, ImageOps
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot

from config import THUMBNAIL_SIZE, THUMBNAIL_DIR


class _ThumbnailTask(QRunnable):
    """
    One thumbnail job executed by QThreadPool.
    """

    def __init__(self, worker, item_path, source_path):
        super().__init__()
        self.worker = worker
        self.item_path = str(item_path)
        self.source_path = Path(source_path)
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):

        if self.worker.stop_event.is_set():
            self.worker.task_finished()
            return

        try:
            cache_path = self.worker.create_thumbnail(self.source_path)

            if cache_path and not self.worker.stop_event.is_set():
                self.worker.thumbnail_ready.emit(self.item_path, str(cache_path))

        finally:
            self.worker.task_finished()


class ThumbnailWorker(QObject):
    """
    Parallel thumbnail generator using QThreadPool.

    The public API intentionally remains close to the old QThread worker:
        start()
        isRunning()
        stop()
        thumbnail_ready
        finished

    Worker threads never create QPixmap. They only decode/resize images and
    write the PNG cache. QPixmap is created in MainWindow's GUI thread.
    """

    thumbnail_ready = pyqtSignal(str, str)
    finished = pyqtSignal()

    def __init__(self, items):
        super().__init__()

        self.items = list(items)
        self.stop_event = Event()

        self.pool = QThreadPool(self)

        # 4 workers is a good default for a GUI application.
        # Override with environment variable if needed:
        # THUMBNAIL_WORKERS=6
        worker_count = 8

        self.pool.setMaxThreadCount(max(1, worker_count))

        self._remaining = 0
        self._remaining_lock = Lock()
        self._finished_emitted = False

    def cache_file(self, path):
        """
        Stable cache filename based on absolute path, mtime and size.
        """

        try:
            stat = path.stat()

            key = (
                f"{path.resolve()}"
                f"|{stat.st_mtime_ns}"
                f"|{stat.st_size}"
            ).encode("utf-8")

        except OSError:
            key = str(path.resolve()).encode("utf-8")

        # SHA1 is unnecessary here. A short BLAKE2 hash is faster and
        # collision probability is still negligible for this cache.
        digest = hashlib.blake2b(key, digest_size=10,).hexdigest()

        return THUMBNAIL_DIR / f"{digest}.png"

    def create_thumbnail(self, path):
        """
        Create or reuse a cached thumbnail.

        Returns the cache filename.
        """

        if self.stop_event.is_set():
            return None

        cache = self.cache_file(path)

        # Fast path: cache already exists.
        if cache.is_file():
            return cache

        try:
            with Image.open(path) as image:

                if self.stop_event.is_set():
                    return None

                # For JPEG/TIFF, Pillow can often decode a smaller source
                # directly instead of fully decoding a huge camera image.
                try:
                    image.draft(
                        "RGB",
                        (
                            THUMBNAIL_SIZE * 2,
                            THUMBNAIL_SIZE * 2,
                        ),
                    )
                except Exception:
                    pass

                if self.stop_event.is_set():
                    return None

                image = ImageOps.exif_transpose(image)

                if image.mode not in ("RGB", "RGBA"):
                    if "A" in image.getbands():
                        image = image.convert("RGBA")
                    else:
                        image = image.convert("RGB")

                # BILINEAR is considerably cheaper than BICUBIC and is
                # sufficient for small UI thumbnails.
                image.thumbnail(
                    (
                        int(THUMBNAIL_SIZE * 1.5),
                        int(THUMBNAIL_SIZE * 1.5),
                    ),
                    Image.Resampling.BILINEAR,
                )

                if self.stop_event.is_set():
                    return None

                cache.parent.mkdir(parents=True, exist_ok=True)

                # PNG optimization is CPU-heavy and unnecessary for
                # thumbnails. Low compression is intentionally used here.
                image.save(
                    cache,
                    "PNG",
                    optimize=False,
                    compress_level=1,
                )

                return cache

        except Exception:
            return None

    def start(self):
        """
        Submit thumbnail jobs to QThreadPool.
        """

        THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)

        # If several tree items use the same source image, decode it once.
        unique = {}

        for item_path, source_path in self.items:

            try:
                source_key = str(Path(source_path).resolve())
            except Exception:
                source_key = str(source_path)

            unique.setdefault(
                source_key,
                []
            ).append((str(item_path), source_path))

        jobs = []

        for entries in unique.values():
            # One source may belong to multiple tree items.
            # The cache prevents duplicate actual image generation.

            for item_path, source_path in entries:
                jobs.append(_ThumbnailTask(self, item_path, source_path))

        self._remaining = len(jobs)

        if not jobs:
            self._emit_finished()
            return

        for job in jobs:
            self.pool.start(job)

    def task_finished(self):
        """
        Called by every QRunnable when it exits.
        """

        with self._remaining_lock:
            self._remaining -= 1

            if self._remaining <= 0:
                self._remaining = 0
                self._emit_finished_locked()

    def _emit_finished(self):
        with self._remaining_lock:
            self._emit_finished_locked()

    def _emit_finished_locked(self):
        if self._finished_emitted:
            return

        self._finished_emitted = True
        self.finished.emit()

    def isRunning(self):
        """
        Compatibility with the old QThread-based implementation.
        """

        return self._remaining > 0

    def stop(self):
        """
        Stop pending work.

        Running jobs cannot be forcibly killed safely, but they check the
        event between expensive operations. Queued jobs are removed.
        """

        self.stop_event.set()
        self.pool.clear()

    def deleteLater(self):
        """
        Stop the worker and let QObject clean itself up.

        QThreadPool keeps the running QRunnable objects alive until they
        finish, so the worker remains referenced by active jobs.
        """

        self.stop()
        super().deleteLater()
