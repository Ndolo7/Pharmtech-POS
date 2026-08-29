#!/usr/bin/env python
import os
import sys
from pathlib import Path

# Setup Django environment
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pos_system.settings")

import django
django.setup()

from products.management.commands.exempt_zero_movement_stock import Command

if __name__ == "__main__":
    cmd = Command()
    cmd.run_from_argv([sys.argv[0], "exempt_zero_movement_stock"] + sys.argv[1:])
