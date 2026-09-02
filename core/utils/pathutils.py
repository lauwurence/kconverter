################################################################################
##

from pathlib import Path


def directory_signature(folder):
    """
    Быстрый snapshot директории.
    """

    folder = Path(folder).resolve()

    try:
        entries = []

        for entry in folder.iterdir():

            try:
                stat = entry.stat()

                entries.append((
                    entry.name,
                    entry.is_dir(),
                    stat.st_mtime_ns,
                    stat.st_size,
                ))

            except OSError:
                entries.append((
                    entry.name,
                    entry.is_dir(),
                    None,
                    None,
                ))

        entries.sort()

        return tuple(entries)

    except OSError:
        return None

