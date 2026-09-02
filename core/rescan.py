################################################################################
## Rescan

import os

from threading import Event
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal


class RescanWorker(QThread):
    finished = pyqtSignal(object, object, object)
    error = pyqtSignal(str)

    def __init__(self, folders, mode="full", changed_paths=None):
        super().__init__()
        self.folders = folders
        self.mode = mode
        self.changed_paths = set(changed_paths or ())
        self.stop_event = Event()

    def stop(self):
        self.stop_event.set()

    @staticmethod
    def directory_signature(folder):
        folder = Path(folder).resolve()
        try:
            entries = []
            for entry in folder.iterdir():
                if entry.name.lower() == "backup":
                    continue
                try:
                    st = entry.stat()
                    entries.append((
                        entry.name,
                        entry.is_dir(),
                        st.st_mtime_ns,
                        st.st_size,
                    ))
                except OSError:
                    entries.append((entry.name, entry.is_dir(), None, None))
            entries.sort()
            return tuple(entries)
        except OSError:
            return None

    @staticmethod
    def image_signature(folder):
        folder = Path(folder).resolve()
        result = []
        try:
            for source in folder.rglob("*.png"):
                if not source.is_file() or " - " in source.stem:
                    continue
                try:
                    st = source.stat()
                    result.append((
                        source.relative_to(folder).as_posix(),
                        st.st_mtime_ns,
                        st.st_size,
                    ))
                except OSError:
                    pass
        except OSError:
            return None
        result.sort()
        return tuple(result)

    def build_snapshots(self):
        snapshots = {}
        watched = set()

        for settings in self.folders:
            root = Path(settings.source_folder).resolve()

            if not root.is_dir():
                continue

            try:
                for base, dirs, _ in os.walk(root):
                    if self.stop_event.is_set():
                        return watched, snapshots

                    dirs[:] = [
                        d for d in dirs
                        if d.lower() != "backup"
                    ]

                    base = str(Path(base).resolve())
                    watched.add(base)
                    snapshots[base] = self.directory_signature(base)

            except OSError:
                pass

        return watched, snapshots

    def run(self):
        try:
            watched, snapshots = self.build_snapshots()

            if self.stop_event.is_set():
                return

            self.finished.emit(
                self.mode,
                self.changed_paths,
                {
                    "watched": watched,
                    "snapshots": snapshots,
                },
            )

        except Exception as exc:
            self.error.emit(str(exc))