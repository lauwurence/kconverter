################################################################################
## Crop Transparency Worker

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal


class CropTransparencyWorker(QThread):
    finished = pyqtSignal()
    progress = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, folder, parent=None, max_workers=None):
        super().__init__(parent)

        self.folder = Path(folder)
        self.max_workers = max_workers or min(8, os.cpu_count() or 1)

    def crop_file(self, file, output_folder):
        """
        Обработка одного PNG.
        Выполняется в отдельном потоке ThreadPoolExecutor.
        """

        try:
            with Image.open(file) as image:
                image = image.convert('RGBA')

                bbox = image.getchannel('A').getbbox()

                if bbox is None:
                    return {
                        'status' : 'skipped',
                        'message' : f"Skipped: {file.name}",
                    }

                cropped = image.crop(bbox)

                output_file = output_folder / file.name
                cropped.save(output_file, format="PNG")

                return {
                    'status' : 'ok',
                    'message' : f"{file.name}: {image.width}x{image.height} -> {cropped.width}x{cropped.height}",
                }

        except Exception as e:
            return {
                'status' : 'error',
                'message' : f"{file.name}: {e}",
            }

    def run(self):
        folder = self.folder

        try:
            output_folder = folder
            output_folder.mkdir(exist_ok=True)

            files = sorted(folder.glob("*.png"), key=lambda x: x.name)

            if not files:
                self.progress.emit("PNG files not found")
                return

            self.progress.emit(f"Cropping {len(files)} files using {self.max_workers} threads...")

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = [ executor.submit(self.crop_file, file, output_folder) for file in files ]

                for future in as_completed(futures):
                    result = future.result()

                    status = result["status"]
                    message = result["message"]

                    if status == "error":
                        self.error.emit(message)
                    else:
                        self.progress.emit(message)

            self.progress.emit("Done cropping!")

        except Exception as e:
            self.error.emit(str(e))

        finally:
            self.finished.emit()
