"""Process-wide handles set during app startup.

Kept in a tiny module so routes can reach the poller registry without importing
`main` (which would be a circular import) and without threading a dependency
through every signature.
"""

from __future__ import annotations

from .infra.poller import PollerRegistry
from .services.show_runner import ShowRunner

registry: PollerRegistry | None = None
runner: ShowRunner | None = None


def autoplay_on(show_id: int) -> bool:
    """Whether a runner task is driving this show right now.

    Read through the module rather than captured, so a test that swaps the
    runner mid-flight is not answering with a stale reference.
    """
    return runner is not None and runner.is_running(show_id)
