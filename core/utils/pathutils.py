################################################################################
## Pathutils

from pathlib import Path

def iter_files(folder, suffix):

    if not isinstance(suffix, list):
        suffix = [ suffix ]

    try:

        for path in Path(folder).resolve().rglob("*"):

            if not path.is_file():
                continue

            if path.suffix.lower() in suffix:
                yield path

    except OSError:
        return
