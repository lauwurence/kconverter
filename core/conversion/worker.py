################################################################################
## Conversion Thread

import os

from time import time
from threading import Event
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

from collections import deque
from time import monotonic
from threading import Event, Lock

from .image import ImageConverter
from .webm import WebMConverter


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
        self.progress_start = monotonic()
        self.progress_lock = Lock()

        # Последние измерения: (timestamp, completed)
        self.progress_samples = deque(maxlen=30)

        self.eta = 0.0

    def stop(self):
        self.stop_event.set()


    def add_progress(self, done, total):
        with self.progress_lock:
            self.progress_done += done

            now = monotonic()
            current_done = self.progress_done
            current_total = self.progress_total

            self.progress_samples.append((now, current_done))

            # Недостаточно данных для адекватной оценки.
            if len(self.progress_samples) < 2:
                eta = 0.0

            else:
                old_time, old_done = self.progress_samples[0]
                dt = now - old_time
                dd = current_done - old_done

                if dt <= 0 or dd <= 0:
                    eta = self.eta
                else:
                    # Скорость только за последнее окно.
                    speed = dd / dt

                    remaining = max(0, current_total - current_done)
                    new_eta = remaining / speed

                    # Сглаживание ETA.
                    if self.eta <= 0:
                        eta = new_eta
                    else:
                        eta = self.eta * 0.7 + new_eta * 0.3

                    self.eta = eta

            self.progress.emit(
                current_done,
                current_total,
                max(0.0, eta),
            )


    def run(self):
        changed_folders = set()

        try:
            total_jobs = len(self.jobs)
            planned_units = []

            for settings, preset, folder, local_settings in self.jobs:

                if settings.mode == "Images":
                    count = 0

                    try:
                        root = Path(folder)

                        for base, dirs, names in os.walk(root):
                            count += sum(1 for name in names if Path(name).suffix.lower() in ImageConverter.INPUT_SUFFIXES)

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

            else:
                self.progress.emit(max(1, self.progress_total), max(1, self.progress_total), 0)
                self.message.emit("All conversions completed.")

        except Exception as exc:
            self.error.emit(str(exc))

        finally:
            self.finished_signal.emit(changed_folders)
