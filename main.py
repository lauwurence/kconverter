################################################################################
## Main

import sys
import ctypes

from PyQt6.QtWidgets import QApplication
from core.main_window import MainWindow

from config import APPID

def main():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APPID)
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
