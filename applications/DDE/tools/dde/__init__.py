"""dde — the execution surface for dde agent tooling.

The CLI owns execution, rate limits and leases, retry, provenance
stamping, artifact naming, and threshold values. Routing lives in skill
descriptions, not here: never assume an agent will read `dde --help`
to find its way (docs/tool-design-guidance.md §1).
"""

from .core.env import CLI_VERSION

__version__ = CLI_VERSION
