import os

# Run Qt widgets without a display (CI / headless test runs)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
