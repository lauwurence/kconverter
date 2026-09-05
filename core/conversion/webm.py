################################################################################
## WebM Converter

import re
import pickle
import hashlib
import subprocess
import ffmpeg

from datetime import datetime
from threading import Event
from pathlib import Path
from math import ceil

from PIL import Image, ImageCms, ImageEnhance, ImageFilter

from config import WEBM_CACHE_FILE, MINTERPOLATE, RESAMPLE, PROFILE_SRGB

from ..local_webm import normalize_webm_settings
from ..utils import textutils


class WebMConverter():

    IMAGE_EXTENSIONS = {".jpg", ".png", ".webp"}

    PREVIEW_QUALITY = 95

    EXIF_DATA = {
        (315,): "keyclap",
        (33432,): f"Copyright {datetime.now().year} keyclap. All Rights Reserved.",
    }


    def __init__(
        self,
        folder,
        preset,
        local_settings=None,
        stop_event=None,
        progress_callback=None,
        source_root=None,
    ):
        self.folder = Path(folder).resolve()
        self.source_root = (
            Path(source_root).resolve()
            if source_root
            else self.folder
        )
        self.preset = preset
        self.settings = normalize_webm_settings(preset.webm)

        if local_settings:
            self.settings.update(normalize_webm_settings(local_settings))

        self.settings = normalize_webm_settings(self.settings)
        self.stop_event = stop_event or Event()
        self.process = None
        self.log = print
        self.progress_callback = progress_callback
        output = Path(preset.output_folder)

        if not output.is_absolute():
            output = self.folder.parent / output

        self.output_folder = output.resolve()


    def get_images(self):
        images = [
            path
            for path in self.folder.iterdir()
            if path.is_file()
            and path.suffix.lower() in self.IMAGE_EXTENSIONS
        ]

        def natural_sort(path):
            numbers = re.findall(r"\d+", path.stem)

            if numbers:
                return (0, int(numbers[-1]), path.stem.lower())

            return (1, 0, path.stem.lower())

        images.sort(key=natural_sort)

        return images


    def get_output_folder(self):
        try:
            relative = self.folder.relative_to(self.source_root)
        except ValueError:
            relative = Path(self.folder.name)

        output_folder = self.output_folder / relative.parent

        return output_folder / f"{self.folder.name}"


    def get_output_file(self):
        try:
            relative = self.folder.relative_to(self.source_root)
        except ValueError:
            relative = Path(self.folder.name)

        output_folder = self.output_folder / relative.parent

        return output_folder / f"{self.folder.name}{self.preset.suffix}.webm"


    def get_preview_file(self):
        try:
            relative = self.folder.relative_to(self.source_root)
        except ValueError:
            relative = Path(self.folder.name)

        output_folder = self.output_folder / relative.parent

        return output_folder / f"{self.folder.name}{self.preset.suffix}.jpg"


    def get_cache_file(self):
        return self.folder / WEBM_CACHE_FILE


    def get_size(self):

        output = self.get_output_file()

        if output.exists():
            try:
                return output.stat().st_size
            except OSError:
                pass

        return 0


    def resolution(self):

        if self.settings["resize_mode"] == "Resolution":
            return self.settings["resolution_width"], self.settings["resolution_height"]

        return None


    def read_cache(self):

        try:
            with open(self.get_cache_file(), "rb") as file:
                data = pickle.load(file)

            if not isinstance(data, dict):
                return {}

            return data

        except Exception:
            return {}


    def write_cache(self, signature):

        try:
            with open(self.get_cache_file(), "wb") as file:
                pickle.dump(
                    {
                        "version": 1,
                        "signature": signature,
                    },
                    file,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )

        except OSError as exc:
            self.log(f"WebM cache warning: {exc}")


    def get_source_signature(self, images):

        frames = []

        for image in images:

            try:
                stat = image.stat()
            except OSError:
                continue

            frames.append(
                (
                    image.resolve().as_posix(),
                    stat.st_mtime_ns,
                    stat.st_size,
                )
            )

        data = (
            2,
            tuple(frames),
            tuple(sorted(self.settings.items())),
            (
                self.preset.suffix,
                self.preset.output_folder,
            ),
        )

        serialized = pickle.dumps(
            data,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

        return hashlib.sha256(serialized).hexdigest()


    def needs_conversion(self, images):
        output = self.get_output_file()
        preview = self.get_preview_file()

        if not output.exists():
            return True, "output does not exist"

        if not preview.exists():
            return True, "preview does not exist"

        cache = self.read_cache()
        signature = self.get_source_signature(images)

        if cache.get("signature") != signature:
            return True, "frames or settings changed"

        try:
            if output.stat().st_size <= 0:
                return True, "output file is empty"

        except OSError:
            return True, "output file cannot be read"

        try:
            if preview.stat().st_size <= 0:
                return True, "preview file is empty"

        except OSError:
            return True, "preview file cannot be read"

        return False, "nothing changed"


    def save_preview(self, image, output_file):
        """
        Save first WebM frame as JPEG preview.
        """

        output_file.unlink(missing_ok=True)

        resolution = self.resolution()
        sharpen = int(self.settings["sharpen"])
        sharpen_radius = float(self.settings["sharpen_radius"])
        downsample = float(self.settings["downsample"])

        with Image.open(image) as img:

            if resolution is None:
                width = img.width / downsample
                height = img.height / downsample
            else:
                width, height = resolution

            img.thumbnail((ceil(width), ceil(height)), resample=RESAMPLE, reducing_gap=None)

            if sharpen and sharpen_radius:
                img = img.filter(
                    ImageFilter.UnsharpMask(
                        radius=sharpen_radius,
                        percent=int(sharpen * 100),
                        threshold=0,
                    )
                )

            icc_profile = img.info.get("icc_profile", PROFILE_SRGB)

            exif = img.getexif()

            for (index,), value in self.EXIF_DATA.items():
                exif[index] = value

            rgb_img = img.convert("RGB")

            rgb_img.save(
                output_file,
                format="JPEG",
                quality=self.PREVIEW_QUALITY,
                compression="jpeg",
                icc_profile=icc_profile,
                exif=exif,
            )

        self.log(f"Preview saved: {output_file}")


    def create_concat_file(self, images):
        digest = hashlib.sha1(str(self.folder).encode()).hexdigest()
        concat_file = self.folder / f".kconverter_{digest}.txt"
        fps = float(self.settings["input_fps"]) * float(self.settings["speed"])
        duration = 1.0 / fps

        with open(concat_file, "w", encoding="utf-8") as file:

            for image in images:
                path = image.resolve().as_posix().replace("'", "'\\''")
                file.write(f"file '{path}'\n")
                file.write(f"duration {duration:.12f}\n")

            if images:
                path = images[-1].resolve().as_posix().replace("'", "'\\''")
                file.write(f"file '{path}'\n")

        return concat_file


    def build_filters(self):
        settings = self.settings
        input_fps = float(settings["input_fps"])
        output_fps = float(settings["output_fps"])
        speed = float(settings["speed"])
        effective_fps = input_fps * speed
        interpolate = int(settings["interpolate"])
        filters = []

        # Minterpolate
        if effective_fps < output_fps:

            if interpolate == 0:
                filters.append(f"fps={effective_fps:g}")

            elif interpolate in MINTERPOLATE:
                filters.append(MINTERPOLATE[interpolate].format(output_fps))

        else:
            filters.append(f"fps={output_fps:g}")

        # Scale
        resolution = self.resolution()

        if resolution:
            filters.append(
                f"scale={resolution[0]}:{resolution[1]}:"
                f"flags=lanczos:param0=4"
            )

        elif settings["resize_mode"] == "Downsample" and float(settings["downsample"]) != 1.0:
            downsample = float(settings["downsample"])
            filters.append(
                f"scale=iw/{downsample:g}:"
                f"ih/{downsample:g}:"
                f"flags=lanczos:param0=4"
            )

        # Sharpen
        sharpen = max(-2, min(5, int(settings["sharpen"]) / 100.0))
        sharpen_radius = max(3, min(23, round(float(settings["sharpen_radius"] * 3))))

        if sharpen:
            filters.append(
                f"unsharp="
                f"luma_msize_x={sharpen_radius}:"
                f"luma_msize_y={sharpen_radius}:"
                f"luma_amount={sharpen}"
            )

        return filters


    def run_process(self, command):

        startupinfo = None
        creationflags = 0

        if __import__("sys").platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
            creationflags=creationflags,
        )

        while True:

            if self.stop_event.is_set():
                self.process.terminate()

                try:
                    self.process.wait(timeout=3)

                except subprocess.TimeoutExpired:
                    self.process.kill()

                raise InterruptedError("WebM conversion stopped.")

            line = self.process.stdout.readline()

            if line:
                self.log(line.rstrip())

            if self.process.poll() is not None:
                break

        if self.process.returncode != 0:
            raise RuntimeError(
                f"FFmpeg exited with code {self.process.returncode}"
            )

        self.process = None


    def run(self):

        images = self.get_images()

        if not images:
            self.log(f"No images found: {self.folder}")

            if self.progress_callback:
                self.progress_callback(1, 1)

            return

        if self.progress_callback:
            self.progress_callback(0, 1)

        self.get_output_file().parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        should_convert, reason = self.needs_conversion(images)

        if not should_convert:
            self.log(
                f"WebM skipped: {self.folder.name} "
                f"(frames and settings unchanged)"
            )

            if self.progress_callback:
                self.progress_callback(1, 1)

            return

        self.log(f"WebM conversion required: {reason}")

        settings = self.settings

        # -------------------------------------------------------------------------
        # Preview
        #
        # Determine the first frame of the resulting WebM BEFORE reversing
        # the image list.
        #
        # Normal:
        #     001 -> 002 -> 003
        #     preview = 001
        #
        # Reverse:
        #     003 -> 002 -> 001
        #     preview = 003
        # -------------------------------------------------------------------------

        preview_frame = images[-1] if settings["reverse"] else images[0]
        preview = self.get_preview_file()

        # Save preview BEFORE WebM conversion.
        self.save_preview(
            preview_frame,
            preview,
        )

        # -------------------------------------------------------------------------
        # Reverse
        # -------------------------------------------------------------------------

        if settings["reverse"]:
            images.reverse()

        # -------------------------------------------------------------------------
        # Concat
        # -------------------------------------------------------------------------

        concat_file = self.create_concat_file(images)

        output = self.get_output_file()
        filters = self.build_filters()

        input_stream = ffmpeg.input(
            str(concat_file),
            format="concat",
            safe=0,
        )

        # -------------------------------------------------------------------------
        # FFmpeg parameters
        # -------------------------------------------------------------------------

        params = {
            "c:v": settings["codec"],
            "pix_fmt": settings["pix_fmt"],
            "threads": settings["threads"],
            "row-mt": settings["row_mt"],
            "tile-columns": settings["tile_columns"],
            "tile-rows": settings["tile_rows"],
            "frame-parallel": 1,
            "cpu-used": settings["cpu_used"],
            "crf": settings["crf"],
            "b:v": 0,
            "color_range": "pc",
            "color_primaries": "bt709",
            "color_trc": "bt709",
            "colorspace": "bt709",
            "map_metadata": -1,
            "f": "webm",
            "an": None,
        }

        if filters:
            params["vf"] = ",".join(filters)

        if settings["loop"]:
            params["loop"] = 0

        # -------------------------------------------------------------------------
        # Build command
        # -------------------------------------------------------------------------

        command = ffmpeg.compile(
            input_stream.output(
                str(output),
                **params
            ),
            overwrite_output=True,
        )

        # -------------------------------------------------------------------------
        # Log
        # -------------------------------------------------------------------------

        self.log(f"WebM: {self.folder.name}")
        self.log(f"Output: {output}")
        self.log(f"Preview: {preview}")
        self.log(f"Frames: {len(images)}")

        if settings["resize_mode"] == "Resolution":
            self.log(
                f"Resolution: "
                f"{settings['resolution_width']}x"
                f"{settings['resolution_height']}"
            )
        else:
            self.log(
                f"Downsample: {settings['downsample']}x"
            )

        self.log(
            f"FPS: {settings['input_fps']} -> "
            f"{settings['output_fps']}"
        )

        self.log(f"CRF: {settings['crf']}")

        # -------------------------------------------------------------------------
        # Convert WebM
        # -------------------------------------------------------------------------

        try:
            self.run_process(command)

        finally:
            concat_file.unlink(missing_ok=True)

        # -------------------------------------------------------------------------
        # Save cache
        # -------------------------------------------------------------------------

        if output.exists():

            self.write_cache(
                self.get_source_signature(images)
            )

            self.log(
                f"Finished: {output} "
                f"({textutils.format_size(output.stat().st_size)})"
            )

        # -------------------------------------------------------------------------
        # Progress
        # -------------------------------------------------------------------------

        if self.progress_callback:
            self.progress_callback(1, 1)