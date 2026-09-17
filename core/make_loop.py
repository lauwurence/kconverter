################################################################################
## Make Loop

from pathlib import Path

import shutil


def make_loop(path):

    path = Path(path)

    files = sorted(path.glob("*.png"), key=lambda x: int(x.stem))

    if not files:
        print("PNG files not found")
        return

    # Последний номер исходного файла
    last_number = int(files[-1].stem)

    # Сохраняем количество цифр исходного формата
    number_width = len(files[-1].stem)

    reverse_files = files[-2:0:-1]

    for i, file in enumerate(reverse_files, start=last_number + 1):
        new_name = f"{i:0{number_width}d}{file.suffix}"
        destination = path / new_name

        shutil.copy2(file, destination)

        print(f"{file.name} -> {destination.name}")
