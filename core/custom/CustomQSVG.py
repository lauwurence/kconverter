from __future__ import annotations

from time import monotonic
from weakref import WeakSet

from PyQt6.QtCore import (
    QObject,
    QTimer,
    QSize,
    QRectF,
    Qt,
)
from PyQt6.QtGui import (
    QIcon,
    QIconEngine,
    QPainter,
    QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer


class _SvgAnimationManager(QObject):
    """
    Один глобальный таймер для всех анимированных SVG.
    """

    _instance = None

    @classmethod
    def instance(cls) -> "_SvgAnimationManager":
        if cls._instance is None:
            cls._instance = cls()

        return cls._instance

    def __init__(self):
        # QObject должен быть инициализирован ровно один раз.
        super().__init__()

        self._icons = WeakSet()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)  # ~60 FPS
        self._timer.timeout.connect(self._tick)

    def add(self, icon):
        self._icons.add(icon)

        if not self._timer.isActive():
            self._timer.start()

    def remove(self, icon):
        self._icons.discard(icon)

        if not self._icons:
            self._timer.stop()

    def _tick(self):
        # Делаем копию, потому что WeakSet может измениться
        # во время обработки.
        for icon in tuple(self._icons):
            icon._advance()


class _AnimatedSvgEngine(QIconEngine):

    def __init__(
        self,
        svg_path: str,
        widget=None,
        size: QSize = QSize(24, 24),
    ):
        super().__init__()

        self.svg_path = svg_path
        self.widget = widget
        self.size = size

        self.renderer = QSvgRenderer(svg_path)

        self.manager = _SvgAnimationManager.instance()

        self.running = False

        # Время начала анимации.
        self._start_time = monotonic()

        # Последний отрисованный кадр.
        self._current_frame = 0

    def start(self):
        if self.running:
            return

        self.running = True
        self._start_time = monotonic()
        self._current_frame = 0

        if self.renderer.isValid():
            self.renderer.setCurrentFrame(0)

        self.manager.add(self)

        if self.widget is not None:
            self.widget.update()

    def stop(self):
        if not self.running:
            return

        self.running = False
        self.manager.remove(self)

        if self.widget is not None:
            self.widget.update()

    def _advance(self):
        if not self.running:
            return

        if not self.renderer.isValid():
            return

        if not self.renderer.animated():
            return

        duration = self.renderer.animationDuration()
        fps = self.renderer.framesPerSecond()

        if duration <= 0:
            return

        if fps <= 0:
            fps = 60

        # Сколько времени прошло с начала анимации.
        elapsed_ms = (monotonic() - self._start_time) * 1000.0

        # Зацикливаем.
        animation_time = elapsed_ms % duration

        # Переводим время в номер кадра.
        frame = int(animation_time * fps / 1000.0)

        if frame == self._current_frame:
            # Новый кадр ещё не наступил.
            return

        self._current_frame = frame

        self.renderer.setCurrentFrame(frame)

        # ВАЖНО:
        # setIcon() здесь НЕ вызываем.
        # Qt сам вызовет QIconEngine.paint().
        if self.widget is not None:
            self.widget.update()

    def paint(
        self,
        painter: QPainter,
        rect,
        mode: QIcon.Mode,
        state: QIcon.State,
    ):
        if not self.renderer.isValid():
            return

        self.renderer.render(
            painter,
            QRectF(rect),
        )

    def pixmap(
        self,
        size: QSize,
        mode: QIcon.Mode,
        state: QIcon.State,
    ) -> QPixmap:
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)

        self.renderer.render(
            painter,
            QRectF(pixmap.rect()),
        )

        painter.end()

        return pixmap

    def clone(self):
        engine = _AnimatedSvgEngine(
            svg_path=self.svg_path,
            widget=self.widget,
            size=self.size,
        )

        engine._current_frame = self._current_frame

        if self.running:
            engine.start()

        return engine

    def __del__(self):
        try:
            if self.running:
                self.manager.remove(self)
        except Exception:
            pass


class CustomQSVG(QIcon):
    """
    Анимированная SVG-иконка.

    Использование:

        icon = CustomQSVG(
            "icons/status_loading.svg",
            button,
        )

        button.setIcon(icon)
        icon.start()

        icon.stop()
    """

    def __init__(
        self,
        svg_path: str,
        widget=None,
        size: QSize = QSize(24, 24),
    ):
        self._engine = _AnimatedSvgEngine(
            svg_path=svg_path,
            widget=widget,
            size=size,
        )

        super().__init__(self._engine)

    def start(self):
        self._engine.start()

    def stop(self):
        self._engine.stop()

    def isRunning(self):
        return self._engine.running