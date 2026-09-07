"""Shared cross-cutting machinery for the dde CLI.

The CLI is the execution surface: this package owns project-root
resolution, provenance stamping, threshold resolution, stdout budget,
rate limiting and retry. Subcommands own only their domain logic.
"""
