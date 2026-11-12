# main.py
import sys
from PyQt6.QtWidgets import QApplication
from config import EmulatorConfig
from gui import MainWindow

def main():
    # Load settings config
    config = EmulatorConfig()

    # Initialize PyQt6 App
    app = QApplication(sys.argv)
    app.setApplicationName("mGBA Python Replica")
    
    # Initialize Main Window
    window = MainWindow(config)
    window.show()
    
    # Execute App Loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()