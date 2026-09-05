from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QImage, QPixmap

class IconCache:
    _cache = {}

    @classmethod
    def get(cls, path: str, opacity: float = 1.0):
        key = (path, opacity)

        if key not in cls._cache:
            cls._cache[key] = CustomQIcon(path, opacity)

        return cls._cache[key]

class CustomQIcon(QIcon):
    def __init__(self, path: str, opacity: float = 1.0, size: int = 64):
        opacity = max(0.0, min(1.0, opacity))

        # Загружаем SVG через QIcon
        original = QIcon(path)

        # Получаем изображение нужного размера
        pixmap = original.pixmap(size, size)

        # Работаем с QImage
        image = pixmap.toImage().convertToFormat(
            QImage.Format.Format_ARGB32
        )

        # Меняем alpha каждого пикселя
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = image.pixel(x, y)

                alpha = (pixel >> 24) & 0xFF
                new_alpha = int(alpha * opacity)

                image.setPixel(
                    x,
                    y,
                    (pixel & 0x00FFFFFF) | (new_alpha << 24)
                )

        super().__init__(QPixmap.fromImage(image))