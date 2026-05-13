"""Notepad + Copilot CLI desktop tool."""
import sys
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from ui.theme import apply_theme


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Notepad + Copilot")
    app.setStyle("Fusion")
    apply_theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
