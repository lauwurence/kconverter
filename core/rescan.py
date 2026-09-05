################################################################################
## Rescan

import os

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from threading import Event
from PyQt6.QtCore import QThread, pyqtSignal


def _scan_directory(folder):
    """
    Scan one directory using scandir (faster than iterdir/stat per entry).
    """

    folder = Path(folder).resolve()

    entries = []
    child_dirs = []

    try:
        with os.scandir(folder) as it:

            for entry in it:

                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    st = entry.stat(follow_symlinks=False)

                    entries.append((
                        entry.name,
                        is_dir,
                        st.st_mtime_ns,
                        st.st_size,
                    ))

                    if is_dir:
                        child_dirs.append(Path(entry.path).resolve())

                except OSError:

                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        is_dir = False

                    entries.append((entry.name, is_dir, None, None))

                    if is_dir:
                        child_dirs.append(Path(entry.path).resolve())

    except OSError:
        return str(folder), None, []

    entries.sort()
    return str(folder), tuple(entries), child_dirs


class RescanWorker(QThread):

    result_ready = pyqtSignal(object, object, object)
    error = pyqtSignal(str)

    def __init__(self, folders, mode="full", changed_paths=None):
        super().__init__()

        self.setObjectName("RescanWorker")
        self.folders = folders
        self.mode = mode
        self.changed_paths = { str(Path(p).resolve()) for p in (changed_paths or []) }
        self.stop_event = Event()

        default_workers = int(os.cpu_count() * 0.8) or 4

        self.max_workers = default_workers


    def _scan_roots(self, roots):
        """
        Recursively scan directories in parallel.

        Each worker scans one directory and returns its direct subdirectories;
        those are then submitted to the pool. This avoids doing the whole
        os.walk traversal in a single thread.
        """

        snapshots = {}
        watched = set()
        pending = set()
        scheduled = set()

        roots = { Path(root).resolve() for root in roots if Path(root).is_dir() }

        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="Rescan") as executor:

            for root in roots:
                root_str = str(root)
                future = executor.submit(_scan_directory, root)
                pending.add(future)
                scheduled.add(root_str)

            while pending:

                if self.stop_event.is_set():

                    for future in pending:
                        future.cancel()

                    return watched, snapshots

                done, pending = wait(pending, return_when=FIRST_COMPLETED)

                for future in done:

                    if self.stop_event.is_set():
                        break

                    try:
                        path, signature, child_dirs = future.result()

                    except Exception:
                        continue

                    watched.add(path)

                    if signature is not None:
                        snapshots[path] = signature

                    for child in child_dirs:
                        child = Path(child).resolve()
                        child_str = str(child)

                        if child_str in scheduled:
                            continue

                        future_arg = executor.submit(_scan_directory, child)
                        pending.add(future_arg)
                        scheduled.add(child_str)

        return watched, snapshots


    def build_snapshots(self):
        if self.mode == "incremental":
            roots = set()

            for path in self.changed_paths:
                path = Path(path).resolve()

                if path.is_dir():
                    roots.add(path)
                else:
                    roots.add(path.parent)

            return self._scan_roots(roots)

        roots = [ Path(settings.source_folder).resolve() for settings in self.folders if Path(settings.source_folder).is_dir() ]

        return self._scan_roots(roots)


    def run(self):

        try:
            watched, snapshots = self.build_snapshots()

            if self.stop_event.is_set():
                return

            self.result_ready.emit(
                self.mode,
                self.changed_paths,
                {
                    "watched": watched,
                    "snapshots": snapshots,
                },
            )

        except Exception as exc:
            self.error.emit(str(exc))


    def stop(self):
        self.stop_event.set()
