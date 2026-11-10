"""KiCad IPC plugin entry point (referenced by plugin.json).

KiCad runs this file directly, so it must stay at the plugin root. All logic
lives in the ``kipida`` package; this launcher only makes the package
importable and starts the app.
"""
import os
import sys

plugin_dir = os.path.dirname(os.path.abspath(__file__))
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

from kipida.app import main

if __name__ == "__main__":
    main()
