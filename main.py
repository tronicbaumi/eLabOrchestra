"""eLabOrchestra — entry point."""

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from ui import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("eLabOrchestra")
    app.setOrganizationName("eLabOrchestra")
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
