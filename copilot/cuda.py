"""Make the pip-installed NVIDIA DLLs (cuBLAS, cuDNN) visible to CTranslate2 on Windows.

Must be imported before faster_whisper / ctranslate2.
"""

import os
import site
from pathlib import Path


def add_nvidia_dll_dirs():
    for sp in site.getsitepackages():
        for bin_dir in (Path(sp) / "nvidia").glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


if hasattr(os, "add_dll_directory"):  # Windows only
    add_nvidia_dll_dirs()
