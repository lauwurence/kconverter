################################################################################
## WebM Converter

import re
import json
import hashlib
import subprocess
import ffmpeg

from threading import Event
from pathlib import Path

from config import WEBM_CACHE_FILE, MINTERPOLATE

from ..webm import normalize_webm_settings


class WebMConverter():

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


    def __init__(self, folder, preset, local_settings=None, stop_event=None, progress_callback=None):
        self.folder = Path(folder).resolve()
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


    def get_output_file(self):
        return self.output_folder / f"{self.folder.name}{self.preset.suffix}.webm"


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
            with open(self.get_cache_file(), "r", encoding="utf-8") as file:
                data = json.load(file)

            return data if isinstance(data, dict) else {}

        except Exception:
            return {}

    def write_cache(self, signature):

        try:
            with open(self.get_cache_file(), "w", encoding="utf-8") as file:
                json.dump({"version": 1, "signature": signature}, file, ensure_ascii=False, indent=2, sort_keys=True)

        except OSError as exc:
            self.log(f"WebM cache warning: {exc}")


    def get_source_signature(self, images):
        frames = []

        for image in images:
            try:
                stat = image.stat()
            except OSError:
                continue

            frames.append({"path": image.resolve().as_posix(), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size})

        data = {"version": 2, "frames": frames, "settings": self.settings, "preset": {"suffix": self.preset.suffix, "output_folder": self.preset.output_folder}}
        serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

        return hashlib.sha256(serialized).hexdigest()


    def needs_conversion(self, images):
        output = self.get_output_file()

        if not output.exists():
            return True, "output does not exist"

        cache = self.read_cache()
        signature = self.get_source_signature(images)

        if cache.get("signature") != signature:
            return True, "frames or settings changed"
        try:
            if output.stat().st_size <= 0:
                return True, "output file is empty"

        except OSError:
            return True, "output file cannot be read"

        return False, "nothing changed"


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
            filters.append(f"scale={resolution[0]}:{resolution[1]}:flags=lanczos:param0=4")

        elif settings["resize_mode"] == "Downsample" and float(settings["downsample"]) != 1.0:
            downsample = float(settings["downsample"])
            filters.append(f"scale=iw/{downsample:g}:ih/{downsample:g}:flags=lanczos:param0=4")

        # Sharpen
        sharpen = float(settings["sharpen"])

        if sharpen:
            filters.append(f"unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount={sharpen}")

        return filters


    def run_process(self, command):

        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")

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
            raise RuntimeError(f"FFmpeg exited with code {self.process.returncode}")

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

        self.output_folder.mkdir(parents=True, exist_ok=True)
        should_convert, reason = self.needs_conversion(images)

        if not should_convert:
            self.log(f"WebM skipped: {self.folder.name} (frames and settings unchanged)")

            if self.progress_callback:
                self.progress_callback(1, 1)

            return

        self.log(f"WebM conversion required: {reason}")
        settings = self.settings

        if settings["reverse"]:
            images.reverse()

        concat_file = self.create_concat_file(images)
        output = self.get_output_file()
        filters = self.build_filters()
        input_stream = ffmpeg.input(str(concat_file), format="concat", safe=0)
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

        command = ffmpeg.compile(input_stream.output(str(output), **params), overwrite_output=True)
        self.log(f"WebM: {self.folder.name}")
        self.log(f"Output: {output}")
        self.log(f"Frames: {len(images)}")

        if settings["resize_mode"] == "Resolution":
            self.log(f"Resolution: {settings['resolution_width']}x{settings['resolution_height']}")
        else:
            self.log(f"Downsample: {settings['downsample']}x")

        self.log(f"FPS: {settings['input_fps']} -> {settings['output_fps']}")
        self.log(f"CRF: {settings['crf']}")

        try:
            self.run_process(command)
        finally:
            concat_file.unlink(missing_ok=True)

        if output.exists():
            self.write_cache(self.get_source_signature(images))
            self.log(f"Finished: {output} ({self.format_size(output.stat().st_size)})")

        if self.progress_callback:
            self.progress_callback(1, 1)


    def format_size(self, size):

        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"

        if size < 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"

        return f"{size / 1024 / 1024 / 1024:.2f} GB"
