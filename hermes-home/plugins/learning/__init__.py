"""Learning plugin: task ledger, feedback grading and guarded auto-applied lessons."""

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Plugin entry point."""
    try:
        from .learning import register_all
        register_all(ctx)
    except Exception:
        logger.warning("learning: failed to register", exc_info=True)
