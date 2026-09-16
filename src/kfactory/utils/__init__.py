"""Utilities to provide geometrical, fill and DRC violation help.

[fill_tiled][kfactory.utils.fill_tiled] provides a filling algorithm that can use
the `klayout.db.TilingProcessor` to calculate the regions to fill.

[fix_spacing][kfactory.utils.violations.fix_spacing_tiled] uses a region space check to
calculate areas that violate min space violations.
"""

from .difftest import diff, difftest, xor
from .simplify import dsimplify, simplify

__all__ = [
    "diff",
    "difftest",
    "dsimplify",
    "fill_tiled",
    "fix_spacing_minkowski_tiled",
    "fix_spacing_tiled",
    "simplify",
    "xor",
]


def __getattr__(name: str):
    # Optional tiled algorithms require native receiver subclass support.
    # Load their unchanged implementations only when explicitly requested.
    from importlib import import_module

    if name == "fill_tiled":
        module = ".fill"
    elif name in {"fix_spacing_minkowski_tiled", "fix_spacing_tiled"}:
        module = ".violations"
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value
