################################################################################
## Image Converter

import os
import pickle
import re
from io import BytesIO
from time import time
from threading import Event, Thread
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageFilter

from config import PROFILE_SRGB, CACHE_DIR


class ImageConverter():

    INPUT_SUFFIXES = {".png"}
    IGNORE_FOLDERS = {"backup"}
    CACHE_FILE = ".cache"
    RESAMPLE = Image.Resampling.LANCZOS
    PROFILE_SRGB = PROFILE_SRGB
    REGEX_NUMBER = re.compile(r".*?(\d*)_(\d*)")
    REGEX_QUALITY = re.compile(r"\s*(\d*)%")
    EXIF_DATA = {315: "keyclap", 33432: f"Copyright {datetime.now().year} keyclap. All Rights Reserved."}

    def __init__(self, source, preset, stop_event=None, progress_callback=None, source_root=None):
        self.source = Path(source).resolve()
        self.source_root = Path(source_root).resolve() if source_root else self.source
        self.preset = preset
        self.resize_mode = preset.resize_mode
        self.resolution_width = preset.resolution_width
        self.resolution_height = preset.resolution_height
        self.downscale = preset.downscale
        self.target_size = preset.target_size
        self.maximum_quality = preset.max_quality
        self.minimum_quality = preset.min_quality
        self.sharpen_radius = preset.sharpen_radius
        self.sharpen_percent = preset.sharpen_percent
        self.sharpen_threshold = preset.sharpen_threshold
        self.output = self.resolve_output()
        self.suffix = preset.suffix
        self.cache_file = CACHE_DIR / f'{self.CACHE_FILE}_{preset.id}'
        self.cache = {}
        self.output_files = set()
        self.saved_images = 0
        self.source_sizes = []
        self.saved_sizes = []
        self.saved_qualities = []
        self.stop_event = stop_event or Event()
        self.log = print
        self.progress_callback = progress_callback
        self.completed_results = {}
        self.files_total = 0


    def resolve_output(self):
        output = Path(self.preset.output_folder)
        if not output.is_absolute():
            output = self.source_root.parent / output
        return output.resolve()


    def human_size(self, size):
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        return f"{size / 1024 / 1024 / 1024:.2f} GB"


    def read_cache(self):
        try:
            with open(self.cache_file, "rb") as file:
                return pickle.load(file)
        except Exception:
            return {}


    def write_cache(self):
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_file, "wb") as file:
            pickle.dump(self.cache, file, protocol=pickle.HIGHEST_PROTOCOL)


    def get_output_name(self, file):

        found = re.findall(self.REGEX_NUMBER, file.stem)
        quality = re.findall(self.REGEX_QUALITY, file.stem)

        if found and found[0] and found[0][0] and found[0][1]:
            file_number = int(found[0][0])
            file_subnumber = int(found[0][1])
            converted_name = ""
            relative_folder = file.parent.relative_to(self.source_root).as_posix()
            for folder in relative_folder.split("/"):
                if not folder:
                    continue
                if folder == "00_parts":
                    converted_name += "ps"
                elif converted_name == "ps":
                    numbers = re.findall(r"\d+", folder)
                    if numbers:
                        converted_name += numbers[0]
                else:
                    converted_name += "_"
                    converted_name += "".join(char for char in folder if not char.isdigit())
            converted_name = converted_name.replace("__", "_")
            output_name = f"{converted_name}_{file_number:02d}_{file_subnumber:03d}{self.suffix}.jpg"
        else:
            output_name = f"{file.stem}{self.suffix}.jpg"
        multiplier = int(quality[0]) / 100 if quality else 1.0
        multiplier = max(0.0, min(1.0, multiplier))
        return output_name, multiplier

    def get_output_file(self, file):
        output_name, _ = self.get_output_name(file)
        relative_folder = file.parent.relative_to(self.source_root)
        return self.output / relative_folder / output_name

    def scan(self):
        files = {}
        all_files = []

        for folder, dirs, names in os.walk(self.source):
            if self.stop_event.is_set():
                break

            dirs[:] = [
                directory
                for directory in dirs
                if directory.lower() not in self.IGNORE_FOLDERS
            ]

            names.sort()

            for name in names:
                if self.stop_event.is_set():
                    break

                file = Path(folder) / name

                if (
                    not file.is_file()
                    or file.suffix.lower() not in self.INPUT_SUFFIXES
                    or " - " in file.stem
                ):
                    continue

                all_files.append(file)

                output_name, multiplier = self.get_output_name(file)

                relative_folder = file.parent.relative_to(self.source_root)
                output_file = self.output / relative_folder / output_name

                self.output_files.add(output_file)

                # ВАЖНО: всегда относительно project root
                relative_file = file.relative_to(self.source_root).as_posix()

                mod_time = int(file.stat().st_mtime)

                cache_settings = (
                    mod_time,
                    self.preset.cache_key,
                )

                if output_file.exists() and self.cache.get(relative_file) == cache_settings:
                    continue

                files[file] = {
                    "output_file": output_file,
                    "quality": multiplier,
                    "relative_file": relative_file,
                    "mod_time": mod_time,
                }

        self.files_total = len(all_files)

        return files, all_files

    def resize_image(self, image):

        if self.resize_mode == "Resolution":
            return image.resize((self.resolution_width, self.resolution_height), self.RESAMPLE)

        width = max(1, round(image.width / self.downscale))
        height = max(1, round(image.height / self.downscale))

        return image.resize((width, height), self.RESAMPLE)

    def convert_file(self, source, data, index):

        if self.stop_event.is_set():
            return

        output = data["output_file"]
        multiplier = data["quality"]
        relative_file = data["relative_file"]
        mod_time = data["mod_time"]
        quality = self.maximum_quality
        output.parent.mkdir(parents=True, exist_ok=True)

        try:

            with Image.open(source) as image:
                image = self.resize_image(image)
                icc_profile = image.info.get("icc_profile", self.PROFILE_SRGB)
                exif = image.getexif()

                for tag, value in self.EXIF_DATA.items():
                    exif[tag] = value

                rgb = image.convert("RGB")

                if self.sharpen_percent > 0 and self.sharpen_radius > 0:
                    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=self.sharpen_radius, percent=self.sharpen_percent, threshold=self.sharpen_threshold))

                while True:

                    if self.stop_event.is_set():
                        return

                    buffer = BytesIO()
                    rgb.save(buffer, format="JPEG", quality=quality, icc_profile=icc_profile)
                    file_size = buffer.tell()
                    buffer.close()

                    if file_size <= self.target_size * 1024 or quality <= self.minimum_quality:
                        rgb.save(output, format="JPEG", quality=min(100, round(quality * multiplier)), icc_profile=icc_profile, exif=exif)
                        break

                    quality -= 1

            source_size = source.stat().st_size
            saved_size = output.stat().st_size
            result = {
                "index": index,
                "source": source,
                "output": output,
                "width": rgb.width,
                "height": rgb.height,
                "quality": quality,
                "size": saved_size,
                "source_size": source_size,
                "success": True,
                "error": None,
            }

            self.cache[relative_file] = (
                mod_time,
                self.preset.cache_key,
            )

        except Exception as exc:
            result = {"index": index, "source": source, "output": output, "width": 0, "height": 0, "quality": 0, "size": 0, "source_size": 0, "success": False, "error": str(exc)}

        self.completed_results[index] = result

        if self.progress_callback:
            self.progress_callback(1, self.files_total)

        return result

    def run(self):
        start = time()
        self.output.mkdir(parents=True, exist_ok=True)
        self.cache = self.read_cache()
        files, all_files = self.scan()

        if self.progress_callback:
            self.progress_callback(0, self.files_total)

        workers = []
        max_workers = max(2, int((os.cpu_count() or 1) * 0.925))
        indexed_files = list(enumerate(all_files, 1))

        for index, source in indexed_files:

            if self.stop_event.is_set():
                break

            if source not in files:
                self.completed_results[index] = {
                    "index": index,
                    "source": source,
                    "output": self.get_output_file(source),
                    "width": 0,
                    "height": 0,
                    "quality": 0,
                    "size": 0,
                    "source_size": 0,
                    "success": True,
                    "cached": True,
                    "error": None,
                }

                if self.progress_callback:
                    self.progress_callback(1, self.files_total)

                continue

            data = files[source]

            while len(workers) >= max_workers:

                for worker in workers[:]:
                    if not worker.is_alive():
                        worker.join()
                        workers.remove(worker)

                if len(workers) >= max_workers:
                    self.stop_event.wait(0.01)

            worker = Thread(target=self.convert_file, args=(source, data, index))
            worker.start()
            workers.append(worker)

        for worker in workers:
            worker.join()

        self.write_cache()

        for index in range(1, len(all_files) + 1):

            if self.stop_event.is_set():
                break

            result = self.completed_results.get(index)

            if not result:
                continue

            if result.get("cached"):
                output = result["output"]
                self.log(f"{index}/{self.files_total}: {output} | cached")
                continue

            if result["success"]:
                self.saved_images += 1
                self.source_sizes.append(result["source_size"])
                self.saved_sizes.append(result["size"])
                self.saved_qualities.append(result["quality"])
                self.log(f"{index}/{self.files_total}: {result['output']} | {result['width']}x{result['height']} | {result['quality']:.0f}% | {self.human_size(result['size'])}")
            else:
                self.log(f"{index}/{self.files_total}: ERROR: {result['source']} | {result['error']}")

        self.log(f"Images converted: {self.saved_images}")
        self.log(f"Finished in {time() - start:.1f} sec")
