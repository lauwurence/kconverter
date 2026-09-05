################################################################################
## Settings

import pickle

from pathlib import Path

from config import LOCAL_IMAGE_FILE, LOCAL_WEBM_FILE, PERSISTENT_FILE, SAVES_DIR


def read_local_image_settings(folder):
    path = Path(folder).resolve() / LOCAL_IMAGE_FILE

    try:
        with open(path, "rb") as file:
            data = pickle.load(file)

        if isinstance(data, dict):
            return data

    except Exception:
        pass

    return {}


def write_local_image_settings(folder, data):
    path = Path(folder).resolve() / LOCAL_IMAGE_FILE

    if not data:
        path.unlink(missing_ok=True)
        return

    with open(path, "wb") as file:
        pickle.dump(data, file, protocol=pickle.HIGHEST_PROTOCOL)


def read_local_webm_settings(folder):
    path = Path(folder).resolve() / LOCAL_WEBM_FILE

    try:
        with open(path, "rb") as file:
            data = pickle.load(file)

        if isinstance(data, dict):
            return data

    except Exception:
        pass

    return {}


def write_local_webm_settings(folder, data):
    path = Path(folder).resolve() / LOCAL_WEBM_FILE

    if not data:
        path.unlink(missing_ok=True)
        return

    with open(path, "wb") as file:
        pickle.dump(data, file, protocol=pickle.HIGHEST_PROTOCOL)


def write_last_project(filename):

    try:
        SAVES_DIR.mkdir(parents=True, exist_ok=True)
        data = { 'last_project' : str(Path(filename).resolve()) }

        with open(PERSISTENT_FILE, "wb") as file:
            pickle.dump(data, file, protocol=pickle.HIGHEST_PROTOCOL)

    except Exception:
        pass


def read_last_project():

    if not PERSISTENT_FILE.exists():
        return None

    try:
        with open(PERSISTENT_FILE, "rb") as file:
            data = pickle.load(file)

        if isinstance(data, dict):
            return data.get("last_project")

    except Exception:
        pass

    return None
