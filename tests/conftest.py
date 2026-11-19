"""Load pure (Home Assistant free) modules straight from the component folder.

Importing the package would pull in homeassistant, and putting the component
folder on sys.path would let its select.py shadow the stdlib module.
"""
import importlib.util
import sys
import types
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "secure_e7"

PACKAGE = "secure_e7_pure"
_package = types.ModuleType(PACKAGE)
_package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = _package


def load(name: str):
    """Import a module by name, so its relative imports of other pure modules work."""
    return importlib.import_module(f"{PACKAGE}.{name}")
