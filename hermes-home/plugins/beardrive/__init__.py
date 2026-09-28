"""BearDrive plugin: jailed access to the team's shared, synced folder for the email agent."""

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Plugin entry point."""
    try:
        from .tools import register_tools
        register_tools(ctx)
    except Exception:
        logger.warning("BearDrive: failed to register tools", exc_info=True)
