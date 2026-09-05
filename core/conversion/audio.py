from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


SUPPORTED_BITRATES = (64, 96, 128, 160, 192, 224, 256, 384)
AUDIO_EXTENSIONS = {".ogg"}
IMAGE_EXTENSIONS = {".webp", ".jpg", ".jpeg"}
EXCLUDED_CODES = (" -e.", "-e.")


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

            skip = False

            for code in EXCLUDED_CODES:

                if code in path.name:
                    skip = True
                    break

            if skip:
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

    def convert_file(self, source: Path) -> str:
        """
        Конвертирует файл.

        Возвращает:
            "converted" — успешно конвертирован
            "copied"    — конвертация не удалась, скопирован оригинал
            "skipped"   — файл уже существует и overwrite=False
            "failed"    — не удалось ни конвертировать, ни скопировать
        """
        destination = self._output_for(source)

        if destination.exists() and not self.overwrite:
            return "skipped"

        destination.parent.mkdir(parents=True, exist_ok=True)

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(source),
            "-map_metadata", "0",
            "-c:a", "libvorbis",
            "-b:a", f"{self.bitrate}k",
            "-y" if self.overwrite else "-n",
            str(destination),
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                return "converted"

            # ffmpeg мог оставить повреждённый/неполный файл.
            if destination.exists():
                try:
                    destination.unlink()
                except OSError:
                    pass

            # Fallback: просто копируем оригинальный файл.
            try:
                shutil.copy2(source, destination)
                return "copied"

            except Exception as copy_exc:
                self.failed_files.append(
                    (
                        source,
                        destination,
                        f"Conversion failed: {result.stderr.strip()}; "
                        f"Copy failed: {copy_exc}",
                    )
                )
                return "failed"

        except Exception as exc:
            # Ошибка при запуске ffmpeg.
            if destination.exists():
                try:
                    destination.unlink()
                except OSError:
                    pass

            # Пробуем просто скопировать оригинал.
            try:
                shutil.copy2(source, destination)
                return "copied"

            except Exception as copy_exc:
                self.failed_files.append(
                    (
                        source,
                        destination,
                        f"Conversion error: {exc}; "
                        f"Copy error: {copy_exc}",
                    )
                )
                return "failed"

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
                status = "failed"

                try:
                    status = future.result()

                except Exception as exc:
                    self.failed_files.append(
                        (
                            source,
                            self._output_for(source),
                            str(exc),
                        )
                    )

                self.processed_files += 1

                if status in ("converted", "copied", "skipped"):
                    success += 1

                if progress_callback:
                    progress_callback(
                        self.processed_files,
                        self.total_files,
                        0,
                    )

                if message_callback:
                    relative = self._relative(source)

                    if status == "converted":
                        message_callback(f"✓ {relative}")

                    elif status == "copied":
                        message_callback(
                            f"⚠ {relative} — "
                            f"конвертация не удалась, скопирован оригинал"
                        )

                    elif status == "skipped":
                        message_callback(
                            f"↷ {relative} — файл уже существует"
                        )

                    else:
                        message_callback(
                            f"✗ {relative} — "
                            f"не удалось конвертировать или скопировать"
                        )

        # Keep the source tree's images alongside converted audio.
        self._copy_images()

        deleted = self._cleanup_output()

        return {
            "success": success,
            "failed": len(self.failed_files),
            "total": self.total_files,
            "deleted": deleted,
            "changed": success > 0 or deleted > 0,
        }

    def _is_valid_source_file(self, path: Path) -> bool:
        """Проверяет, должен ли файл существовать в output."""
        suffix = path.suffix.lower()

        if suffix in IMAGE_EXTENSIONS:
            return True

        if suffix in AUDIO_EXTENSIONS:
            return not any(code in path.name for code in EXCLUDED_CODES)

        return False

    def _get_expected_output_files(self):
        """Возвращает множество файлов, которые должны быть в output."""
        expected = set()

        # OGG -> OGG
        for source in self.get_ogg_files():
            expected.add(self._output_for(source).resolve())

        # Изображения
        for source in self.input_folder.rglob("*"):
            if not source.is_file():
                continue
            if source.suffix.lower() not in IMAGE_EXTENSIONS:
                continue

            destination = self.output_folder / self._relative(source)
            expected.add(destination.resolve())

        return expected

    def _cleanup_output(self):
        """Удаляет из output файлы, которых нет в исходной структуре."""
        expected_files = self._get_expected_output_files()
        deleted = 0

        if not self.output_folder.exists():
            return deleted

        for path in self.output_folder.rglob("*"):
            if not path.is_file():
                continue

            if path.resolve() not in expected_files:
                try:
                    path.unlink()
                    deleted += 1
                except OSError as exc:
                    self.failed_files.append(
                        (path, path, f"Failed to delete: {exc}")
                    )

        for directory in sorted(
            (p for p in self.output_folder.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass

        return deleted

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
                bitrate = int(getattr(preset, "bitrate", 128))
                if isinstance(local, dict):
                    bitrate = int(local.get("bitrate", bitrate))
                elif isinstance(local, int):
                    bitrate = local

                output_folder = Path(preset.output_folder).resolve()
                try:
                    relative_folder = Path(folder).resolve().relative_to(Path(settings.source_folder).resolve())
                except ValueError:
                    relative_folder = Path()
                output_folder = output_folder / relative_folder

                converter = AudioConverter(
                    input_folder=folder,
                    output_folder=output_folder,
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
