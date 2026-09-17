import os

from time import time
from threading import Event, Lock
from pathlib import Path
from collections import deque
from time import monotonic

from PyQt6.QtCore import QThread, pyqtSignal

from .image import ImageConverter
from .webm import WebMConverter


class ConversionWorker(QThread):

    # ========================================================
    # Signals
    # ========================================================

    message = pyqtSignal(str)
    error = pyqtSignal(str)
    finished_signal = pyqtSignal(object)
    progress = pyqtSignal(int, int, float)

    # ========================================================
    # Init
    # ========================================================

    def __init__(self, jobs):
        super().__init__()

        self.setObjectName("ConversionWorker")

        self.jobs = jobs
        self.stop_event = Event()

        self.progress_done = 0
        self.progress_total = 0
        self.progress_start = monotonic()
        self.progress_lock = Lock()

        self.progress_samples = deque(maxlen=3)
        self.last_mode = None
        self.eta = 0.0

    # ========================================================
    # Stop
    # ========================================================

    def stop(self):
        """
        Request conversion stop.
        """

        self.stop_event.set()

    # ========================================================
    # Progress
    # ========================================================

    def add_progress(self, done, total):
        """
        Called by converters.

        done:
            Number of newly completed units.

        total:
            Total units for current converter.
        """

        with self.progress_lock:
            self.progress_done += done

            now = monotonic()

            current_done = self.progress_done
            current_total = self.progress_total

            self.progress_samples.append((now, current_done))

            # ------------------------------------------------
            # ETA
            # ------------------------------------------------

            if len(self.progress_samples) < 2:
                eta = 0.0

            else:
                old_time, old_done = self.progress_samples[0]

                dt = now - old_time
                dd = current_done - old_done

                if dt <= 0 or dd <= 0:
                    eta = self.eta

                else:
                    # Units per second
                    speed = dd / dt

                    remaining = max(0, current_total - current_done)
                    new_eta = remaining / speed

                    # Smooth ETA
                    if self.eta <= 0:
                        eta = new_eta
                    else:
                        eta = (self.eta * 0.7) + (new_eta * 0.3)

                    self.eta = eta

            # ------------------------------------------------
            # Emit
            # ------------------------------------------------

            self.progress.emit(current_done, current_total, max(0.0, eta))

    # ========================================================
    # Run
    # ========================================================

    def run(self):
        changed_folders = set()

        try:

            # =================================================
            # Calculate total work
            # =================================================

            total_jobs = len(self.jobs)
            planned_units = []

            for settings, preset, folder, local_preset in self.jobs:

                if settings.mode == "Images":
                    converter = ImageConverter(
                        folder,
                        local_preset or preset,
                        source_root=settings.source_folder
                    )

                    files, all_files = converter.scan()

                    planned_units.append(len(files))

                else:
                    planned_units.append(1)

            # =================================================
            # Initialize progress
            # =================================================

            self.progress_total = sum(planned_units)
            self.progress_done = 0
            self.progress_start = monotonic()
            self.progress_samples.clear()
            self.eta = 0.0
            self.progress.emit(0, max(1, self.progress_total), 0.0)

            # =================================================
            # Process jobs
            # =================================================

            for index, job in enumerate(self.jobs, 1):

                # ---------------------------------------------
                # Stop requested?
                # ---------------------------------------------

                if self.stop_event.is_set():
                    break

                # ---------------------------------------------
                # Job
                # ---------------------------------------------

                settings, preset, folder, local_preset = job

                changed_folders.add(str(Path(folder).resolve()))

                self.message.emit(f"[{index}/{total_jobs}] {settings.mode} | {preset.name} | {folder}")

                # ---------------------------------------------
                # Mode changed
                # ---------------------------------------------

                if self.last_mode != settings.mode:
                    self.last_mode = settings.mode

                    if settings.mode == "Images":
                        self.progress_samples = deque(maxlen=100)
                    else:
                        self.progress_samples = deque(maxlen=3)

                    self.eta = 0.0

                # ---------------------------------------------
                # Create converter
                # ---------------------------------------------

                if settings.mode == "Images":
                    converter = ImageConverter(
                        folder,
                        preset,
                        self.stop_event,
                        self.add_progress,
                        source_root=settings.source_folder
                    )

                else:
                    converter = WebMConverter(
                        folder,
                        preset,
                        local_preset,
                        self.stop_event,
                        self.add_progress,
                        source_root=settings.source_folder
                    )

                # ---------------------------------------------
                # Logger
                # ---------------------------------------------

                converter.log = self.message.emit

                # ---------------------------------------------
                # Run converter
                # ---------------------------------------------

                try:
                    converter.run()

                except InterruptedError:
                    self.message.emit("Conversion stopped.")
                    break

            # =================================================
            # Finished / stopped
            # =================================================

            if self.stop_event.is_set():
                self.message.emit("Conversion stopped.")

            else:
                # 100%
                self.progress.emit(max(1, self.progress_total), max(1, self.progress_total), 0.0)
                self.message.emit("All conversions completed.")

        # =====================================================
        # Error
        # =====================================================

        except Exception as exc:
            self.error.emit(str(exc))

        # =====================================================
        # Finally
        # =====================================================

        finally:
            self.finished_signal.emit(changed_folders)
