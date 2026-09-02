################################################################################
## Conversion Thread

import os
from time import time
from threading import Event
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

from .image import ImageConverter
from .webm import WebMConverter
from ..utils import audio


class ConversionWorker(QThread):

    message = pyqtSignal(str)
    error = pyqtSignal(str)
    finished_signal = pyqtSignal(object)
    progress = pyqtSignal(int, int, float)


    def __init__(self, jobs):
        super().__init__()

        self.setObjectName("ConversionWorker")
        self.jobs = jobs
        self.stop_event = Event()
        self.progress_done = 0
        self.progress_total = 0
        self.progress_start = time()
        self.progress_lock = __import__("threading").Lock()


    def stop(self):
        self.stop_event.set()


    def add_progress(self, done, total):

        with self.progress_lock:
            previous_total = self.progress_total

            if total > 0 and previous_total < self.progress_total + total:
                pass

            self.progress_done += done
            elapsed = max(0.001, time() - self.progress_start)
            speed = self.progress_done / elapsed
            remaining = max(0, self.progress_total - self.progress_done)
            eta = remaining / speed if speed > 0 else 0
            current_done = self.progress_done
            current_total = self.progress_total

        self.progress.emit(current_done, current_total, eta)


    def run(self):
        changed_folders = set()

        audio.play("audio/started.ogg")

        try:
            total_jobs = len(self.jobs)
            planned_units = []
            for settings, preset, folder, local_settings in self.jobs:
                if settings.mode == "Images":
                    count = 0
                    try:
                        root = Path(folder)
                        for base, dirs, names in os.walk(root):
                            dirs[:] = [d for d in dirs if d.lower() not in ImageConverter.IGNORE_FOLDERS]
                            count += sum(1 for name in names if Path(name).suffix.lower() in ImageConverter.INPUT_SUFFIXES and " - " not in Path(name).stem)
                    except OSError:
                        count = 0
                    planned_units.append(max(1, count))
                else:
                    planned_units.append(1)
            self.progress_total = sum(planned_units)
            self.progress_done = 0
            self.progress_start = time()
            self.progress.emit(0, max(1, self.progress_total), 0)

            for index, job in enumerate(self.jobs, 1,):

                if self.stop_event.is_set():
                    break

                settings, preset, folder, local_settings = job

                changed_folders.add(str(Path(folder).resolve()))

                self.message.emit(f"[{index}/{total_jobs}] {settings.mode} | {preset.name} | {folder}")

                if settings.mode == "Images":
                    converter = ImageConverter(
                        folder,
                        preset,
                        self.stop_event,
                        self.add_progress,
                        source_root=settings.source_folder)

                else:
                    converter = WebMConverter(
                        folder,
                        preset,
                        local_settings,
                        self.stop_event,
                        self.add_progress)

                converter.log = self.message.emit

                try:
                    converter.run()
                except InterruptedError:
                    self.message.emit("Conversion stopped.")
                    break

            if self.stop_event.is_set():
                self.message.emit("Conversion stopped.")

                audio.play("audio/stopped.ogg")

            else:
                self.progress.emit(max(1, self.progress_total), max(1, self.progress_total), 0)
                self.message.emit("All conversions completed.")

                audio.play("audio/finished.ogg")

        except Exception as exc:
            self.error.emit(str(exc))

        finally:
            self.finished_signal.emit(changed_folders)
