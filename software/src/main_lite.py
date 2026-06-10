"""Entry point for the Squid Lite frontend."""
import argparse
import sys

from qtpy.QtWidgets import QApplication

import squid.core.logging
from squid.application import ApplicationContext

_log = squid.core.logging.get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Squid Lite — simple imaging frontend")
    parser.add_argument("--simulation", action="store_true")
    parser.add_argument("--skip-init", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    app = QApplication(sys.argv)

    import os
    from squid.ui import lite as _lite_pkg
    qss_path = os.path.join(os.path.dirname(_lite_pkg.__file__), "theme.qss")
    try:
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except OSError:
        _log.warning("Could not load theme.qss at %s", qss_path)

    context = ApplicationContext(simulation=args.simulation, skip_init=args.skip_init)
    try:
        gui = context.create_lite_gui()
        gui.show()
        return app.exec_()
    finally:
        context.shutdown()


if __name__ == "__main__":
    sys.exit(main())
