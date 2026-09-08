"""Allow running the eval package as ``python3 -m eval.run_baseline``."""

from .run_baseline import main
import sys

sys.exit(main())
