"""Load pure (Home Assistant free) modules straight from the component folder.

Importing the package would pull in homeassistant, and putting the component
folder on sys.path would let its select.py shadow the stdlib module.
"""
import importlib.util
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "secure_e7"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"secure_e7_{name}", COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
