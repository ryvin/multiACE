import os
import sys

# Make the Klipper-free builder importable as `ace_status` without pulling in
# ace.py (which imports pyserial and Klipper-only relative modules).
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "klipper", "extras")
)
