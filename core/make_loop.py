################################################################################
## Make Loop

from pathlib import Path

import shutil
import re


def make_loop(path):
    path = Path(path)

    # Все PNG
    all_files = list(path.glob("*.png"))

    if not all_files:
        print("PNG files not found")
        return

    # Сортируем по имени, чтобы определить самый первый файл
    all_files.sort()

    first_file = all_files[0]

    # Определяем префикс и номер:
    # r_0001.png -> prefix="r_", number="0001"
    match = re.match(r"^(.*?)(\d+)$", first_file.stem)

    if not match:
        print(f"Не удалось определить номер файла: {first_file.name}")
        return

    prefix = match.group(1)
    number_width = len(match.group(2))

    # Берём только файлы с таким же префиксом
    files = []

    for file in all_files:
        match = re.match(r"^(.*?)(\d+)$", file.stem)

        if not match:
            continue

        file_prefix = match.group(1)

        if file_prefix == prefix:
            files.append(file)

    # Сортируем по числовому номеру
    files.sort(
        key=lambda x: int(re.search(r"(\d+)$", x.stem).group(1))
    )

    if not files:
        print("Matching PNG files not found")
        return

    # Последний номер исходного файла
    last_number = int(
        re.search(r"(\d+)$", files[-1].stem).group(1)
    )

    # Копируем от предпоследнего к первому,
    # не включая первый и последний
    reverse_files = files[-2:0:-1]

    for i, file in enumerate(reverse_files, start=last_number + 1):
        new_name = f"{prefix}{i:0{number_width}d}{file.suffix}"
        destination = path / new_name

        shutil.copy2(file, destination)

        print(f"{file.name} -> {destination.name}")
