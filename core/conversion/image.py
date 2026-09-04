################################################################################
## Image Converter

import os
import pickle
import re
from io import BytesIO
from time import time
from threading import Event, Thread
from pathlib import Path
from PIL import Image, ImageFilter

from config import PROFILE_SRGB, CACHE_DIR, IMAGE_CACHE_FILE, RESAMPLE, EXIF_DATA
from ..utils import textutils

NON_BACKGROUND_OVERSAMPLE = 1.25


class ImageConverter():

    INPUT_SUFFIXES = {".png"}
    PROBABLE_SUFFIXES = {f"@{NON_BACKGROUND_OVERSAMPLE}.webp"}
    REGEX_NUMBER = re.compile(r".*?(\d*)_(\d*)")
    REGEX_QUALITY = re.compile(r"\s*(\d*)%")

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
        self.cache_file = CACHE_DIR / f'{IMAGE_CACHE_FILE}_{preset.id}'
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
                    converted_name += "".join(c for c in folder if not c.isdigit())

            converted_name = converted_name.replace("__", "_")

            output_name = (
                f"{converted_name}_"
                f"{file_number:02d}_"
                f"{file_subnumber:03d}"
                if self.preset.panorama
                else
                f"{converted_name}_"
                f"{file_number:02d}_"
                f"{file_subnumber:03d}"
                f"{self.suffix}"
            )
        else:
            output_name = f"{file.stem}{self.suffix}"

        return output_name


    def get_output_file(self, file):
        output_name = self.get_output_name(file)
        relative_folder = file.parent.relative_to(self.source_root)
        output = self.output / relative_folder / output_name

        if self.preset.panorama:

            for suffix in self.PROBABLE_SUFFIXES:
                path = output.parent / f'{output.name}{suffix}'

                if path.exists():
                    return path

        return output.with_suffix(".jpg")


    def scan(self):
        files = {}
        all_files = []

        for folder, _, names in os.walk(self.source):

            if self.stop_event.is_set():
                break

            names.sort()

            for name in names:

                if self.stop_event.is_set():
                    break

                file = Path(folder) / name

                if not file.is_file() or file.suffix.lower() not in self.INPUT_SUFFIXES:
                    continue

                output_name = self.get_output_name(file) + ".suffix"

                all_files.append(file)

                if self.preset.panorama:

                    try:
                        with Image.open(file) as image:
                            is_background = image.width == 12000 and image.height == 6000

                    except Exception:
                        continue

                    if is_background:
                        output_name = Path(output_name).with_suffix(".jpg").name
                    else:
                        output_name = Path(output_name).with_suffix(".webp")
                        output_name = output_name.with_stem(output_name.stem + f"@{NON_BACKGROUND_OVERSAMPLE}").name

                else:
                    output_name = Path(output_name).with_suffix(".jpg").name

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
                    "relative_file": relative_file,
                    "mod_time": mod_time,
                }

        self.files_total = len(all_files)

        return files, all_files

    def resize_image(self, image):

        if self.resize_mode == "Resolution":
            return image.resize((self.resolution_width, self.resolution_height), RESAMPLE)

        width = max(1, round(image.width / self.downscale))
        height = max(1, round(image.height / self.downscale))

        return image.resize((width, height), RESAMPLE)

    def convert_file(self, source, data, index):

        if self.stop_event.is_set():
            return

        output = data["output_file"]
        relative_file = data["relative_file"]
        mod_time = data["mod_time"]

        output.parent.mkdir(parents=True, exist_ok=True)

        try:

            with Image.open(source) as image:

                if self.preset.panorama:
                    is_background = (image.width == 12000) and (image.height == 6000)

                    if is_background:
                        output_format = "JPEG"
                        output_suffix = ""
                        convert_mode = "RGB"

                        width = self.resolution_width
                        height = self.resolution_height

                    else:
                        oversample = NON_BACKGROUND_OVERSAMPLE
                        output_format = "WEBP"
                        output_suffix = ""
                        convert_mode = "RGBA"

                        width = image.width * (self.resolution_width * oversample / 12000)
                        height = image.height * (self.resolution_height * oversample / 6000)

                else:
                    output_format = "JPEG"
                    output_suffix = self.suffix
                    convert_mode = "RGB"

                    if self.resize_mode == "Resolution":
                        width = self.resolution_width
                        height = self.resolution_height
                    else:
                        width = image.width / self.downscale
                        height = image.height / self.downscale

                target_size = self.target_size
                maximum_quality = self.maximum_quality
                minimum_quality = self.minimum_quality

                width = max(1, int(width))
                height = max(1, int(height))

                # #################################################################
                # Resize
                # #################################################################

                image = image.resize((width, height), resample=RESAMPLE)

                # #################################################################
                # Sharpen
                # #################################################################

                if self.sharpen_percent > 0 and self.sharpen_radius > 0:
                    image = image.filter(
                        ImageFilter.UnsharpMask(
                            radius=self.sharpen_radius,
                            percent=self.sharpen_percent,
                            threshold=self.sharpen_threshold,
                        )
                    )

                icc_profile = image.info.get("icc_profile", PROFILE_SRGB)

                exif = image.getexif()

                for tag, value in EXIF_DATA.items():
                    exif[tag] = value

                if image.mode != convert_mode:
                    converted_image = image.convert(convert_mode)
                else:
                    converted_image = image

                # #################################################################
                # Output filename
                # #################################################################

                output = output.with_suffix(".jpg" if output_format == "JPEG" else ".webp")

                # if output_suffix:
                #     output = output.with_stem(output.stem + output_suffix)

                # #################################################################
                # WebP

                if output_format == "WEBP":
                    quality = 90

                    converted_image.save(
                        output,
                        format="WEBP",
                        quality=quality,
                        lossless=False,
                        method=6,
                        icc_profile=icc_profile,
                        exif=exif,
                    )

                # #################################################################
                # JPEG

                else:

                    quality = maximum_quality

                    while True:

                        if self.stop_event.is_set():
                            return

                        output_buffer = BytesIO()

                        converted_image.save(
                            output_buffer,
                            format="JPEG",
                            quality=quality,
                            icc_profile=icc_profile,
                        )

                        file_size = output_buffer.tell()
                        output_buffer.close()

                        if file_size <= target_size * 1024 or quality <= minimum_quality:
                            final_quality = max(10, min(100, round(quality)))

                            converted_image.save(
                                output,
                                format="JPEG",
                                quality=final_quality,
                                icc_profile=icc_profile,
                                exif=exif,
                            )

                            break

                        quality -= 1

                # #################################################################
                # Result
                # #################################################################

                source_size = source.stat().st_size
                saved_size = output.stat().st_size

                result = {
                    "index": index,
                    "source": source,
                    "output": output,
                    "width": converted_image.width,
                    "height": converted_image.height,
                    "quality": quality,
                    "size": saved_size,
                    "source_size": source_size,
                    "success": True,
                    "error": None,
                }

                # Cache is still relative to project/source root.
                self.cache[relative_file] = (mod_time, self.preset.cache_key)

        except Exception as exc:

            result = {
                "index": index,
                "source": source,
                "output": output,
                "width": 0,
                "height": 0,
                "quality": 0,
                "size": 0,
                "source_size": 0,
                "success": False,
                "error": str(exc),
            }

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
                self.log(f"{index}/{self.files_total}: {result['output']} | {result['width']}x{result['height']} | {result['quality']:.0f}% | {textutils.format_size(result['size'])}")
            else:
                self.log(f"{index}/{self.files_total}: ERROR: {result['source']} | {result['error']}")

        self.log(f"Images converted: {self.saved_images}")
        self.log(f"Finished in {time() - start:.1f} sec")
