from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


SUPPORTED_BITRATES = (32, 64, 96, 128, 160, 192, 224, 256, 384)
AUDIO_EXTENSIONS = {".ogg"}
IMAGE_EXTENSIONS = {".webp", ".jpg", ".jpeg"}
EXCLUDED_SUFFIXES = ("-e", " -e", " - e", "-exclude", " -exclude", " - exclude")


class AudioConverter:
    """Recursive OGG -> OGG/Opus converter preserving folders and file names."""

    def __init__(
        self,
        input_folder,
        output_folder,
        bitrate=128,
        overwrite=False,
        num_threads=None,
        preserve_structure=True,
    ):
        self.input_folder = Path(input_folder).resolve()
        self.output_folder = Path(output_folder).resolve()
        self.bitrate = int(bitrate)
        self.overwrite = overwrite
        self.preserve_structure = preserve_structure
        self.num_threads = max(1, int(num_threads or (os.cpu_count() or 1)))
        self.processed_files = 0
        self.total_files = 0
        self.failed_files = []
        self._validate()

    def _validate(self):
        if self.bitrate not in SUPPORTED_BITRATES:
            raise ValueError(
                f"Unsupported bitrate {self.bitrate}. "
                f"Choose one of: {', '.join(map(str, SUPPORTED_BITRATES))}."
            )
        if not self.input_folder.is_dir():
            raise ValueError(f"Input folder does not exist: {self.input_folder}")
        if self.input_folder == self.output_folder:
            raise ValueError("Input and output folders must be different.")
        try:
            subprocess.run(
                ["ffmpeg", "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("ffmpeg was not found in PATH.") from exc

    def _relative(self, path: Path) -> Path:
        return path.resolve().relative_to(self.input_folder)

    def get_ogg_files(self):
        files = []
        for path in self.input_folder.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            if path.stem.endswith(EXCLUDED_SUFFIXES):
                continue
            files.append(path.resolve())
        return sorted(set(files), key=lambda p: p.as_posix().lower())

    def _output_for(self, source: Path) -> Path:
        rel = self._relative(source)
        if self.preserve_structure:
            return self.output_folder / rel
        return self.output_folder / source.name

    def _copy_images(self):
        for source in self.input_folder.rglob("*"):
            if not source.is_file() or source.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            destination = self.output_folder / self._relative(source)
            if destination.exists() and not self.overwrite:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def convert_file(self, source: Path) -> bool:
        destination = self._output_for(source)
        if destination.exists() and not self.overwrite:
            return True

        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-map_metadata", "0",
            "-c:a", "libvorbis",
            "-b:a", f"{self.bitrate}k",
            "-y" if self.overwrite else "-n",
            str(destination),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            return True
        self.failed_files.append((source, destination, result.stderr.strip()))
        return False

    def convert_all(self, progress_callback=None, message_callback=None, stop_callback=None):
        files = self.get_ogg_files()
        self.total_files = len(files)
        self.output_folder.mkdir(parents=True, exist_ok=True)

        if message_callback:
            message_callback(
                f"Audio: {self.total_files} OGG files, {self.bitrate} kbps, "
                f"preserving folder structure and names."
            )
        if not files:
            self._copy_images()
            return {"success": 0, "failed": 0, "total": 0, "changed": True}

        success = 0
        with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            futures = {executor.submit(self.convert_file, path): path for path in files}
            for future in as_completed(futures):
                if stop_callback and stop_callback():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                source = futures[future]
                ok = False
                try:
                    ok = future.result()
                except Exception as exc:
                    self.failed_files.append((source, self._output_for(source), str(exc)))
                self.processed_files += 1
                if ok:
                    success += 1
                if progress_callback:
                    progress_callback(self.processed_files, self.total_files, 0)
                if message_callback:
                    mark = "✓" if ok else "✗"
                    message_callback(f"{mark} {self._relative(source)}")

        # Keep the source tree's images alongside converted audio.
        self._copy_images()
        return {
            "success": success,
            "failed": len(self.failed_files),
            "total": self.total_files,
            "changed": success > 0,
        }

from PyQt6.QtCore import QThread, pyqtSignal


class AudioConversionWorker(QThread):
    message = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int, int)
    finished_signal = pyqtSignal(set)

    def __init__(self, jobs):
        super().__init__()
        self.jobs = jobs
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        changed = set()
        try:
            for settings, preset, folder, local in self.jobs:
                if self._stop_requested:
                    break
                bitrate = 128
                if isinstance(local, dict):
                    bitrate = int(local.get("bitrate", 128))
                elif isinstance(local, int):
                    bitrate = local

                converter = AudioConverter(
                    input_folder=folder,
                    output_folder=preset.output_folder,
                    bitrate=bitrate,
                    overwrite=True,
                    preserve_structure=True,
                )
                result = converter.convert_all(
                    progress_callback=lambda done, total, eta: self.progress.emit(done, total, eta),
                    message_callback=lambda msg: self.message.emit(str(msg)),
                    stop_callback=lambda: self._stop_requested,
                )
                if result.get("changed"):
                    changed.add(str(Path(folder).resolve()))
                if converter.failed_files:
                    for source, destination, reason in converter.failed_files:
                        self.message.emit(f"✗ {source.name}: {reason}")
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished_signal.emit(changed)
