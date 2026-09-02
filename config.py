################################################################################
## Config

from pathlib import Path
from PIL import Image, ImageCms

DEVELOPER = False

VERSION = (0, 1)
ICON = "icons/icon.svg"
APPID = f'kconverter.{".".join([str(v) for v in VERSION])}'.lower()

LOCAL_DIR = Path(__file__).resolve().parent

THUMBNAIL_SIZE = 56
ROOT_ROW_HEIGHT = 64
FOLDER_ROW_HEIGHT = 32

PROJECT_EXTENSION = ".kcp"
LOCAL_WEBM_FILE = ".kconverter_webm.json"
WEBM_CACHE_FILE = ".kconverter_webm_cache.json"

THUMBNAIL_DIR = LOCAL_DIR / ".thumbnails"
SAVES_DIR = LOCAL_DIR / "saves"
PERSISTENT_FILE = SAVES_DIR / "persistent"

PROFILE_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
RESAMPLE = Image.Resampling.LANCZOS
MINTERPOLATE = {
    1: "minterpolate=fps={}:mi_mode=blend",
    2: "minterpolate=fps={}",
    3: "minterpolate=fps={}:mi_mode=mci:mc_mode=aobmc:me_mode=bilat",
    4: "minterpolate=fps={}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir",
    5: "minterpolate=fps={}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1",
    6: "minterpolate=fps={}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=fdiff",
}
