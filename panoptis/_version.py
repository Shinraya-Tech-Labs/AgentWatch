"""Single source of truth for the package version.

The version lives in `pyproject.toml` and nowhere else. Everything that needs to *display* a version
— the CLI banner, the API's `/health` and root routes, the OpenTelemetry resource, `__init__` — reads
it from the installed distribution metadata rather than carrying its own copy.

This module deliberately imports nothing from `panoptis`. It is imported by `panoptis/__init__`
itself and by submodules that `__init__` in turn imports, so any dependency back into the package
would create an import cycle that only shows up in whichever import order happens to run first.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

#: The distribution name from pyproject.toml. It matches the import name, but
#: `importlib.metadata` keys off the distribution, so `version("panoptis")` still raises
#: PackageNotFoundError when the package was never installed.
_DISTRIBUTION = "panoptis"

try:
    __version__ = _distribution_version(_DISTRIBUTION)
except PackageNotFoundError:  # pragma: no cover
    # The package is being imported from a source tree that was never installed — running
    # `python -m panoptis` straight out of a clone, for instance. Falling back beats raising on
    # import, which would make the package unusable in exactly the situation a contributor is most
    # likely to hit. The placeholder is deliberately not a plausible release number, so it can't be
    # mistaken for one in a bug report.
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
