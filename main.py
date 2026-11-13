# main.py
import sys
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from gba_studio.core.config import EmulatorConfig
from gba_studio.ui.gui import MainWindow

def main():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL)

    config = EmulatorConfig()
    app = QApplication(sys.argv)
    app.setApplicationName("mGBA Python Replica")

    window = MainWindow(config)
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()