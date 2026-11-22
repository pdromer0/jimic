"""The 7-day purge of what the engine of the agent writes in its folder (decision 23).

The engine keeps the transcript of each session in `projects/` (the messages, the answers, what
the tools returned), and may write more in other folders (backups of its configuration and, in
other modes, history, debug logs, todos, snapshots...). Every file older than `DAYS` goes,
wherever it is, except the few the engine needs to work, which carry no content: its
configuration (ids and counters), the policy limits it downloads and the remote settings
(contract of 22-nov-2026, `claude-agent-sdk` 0.2.187).

Ages are absolute (`mtime` against now, in seconds), as the database purge. Symbolic links are
never followed. The log says how many files went, never their names.
"""

import logging
import os
from datetime import timedelta
from pathlib import Path

from jimic.clock import Clock

logger = logging.getLogger(__name__)

DAYS = 7
KEEP = frozenset(
    {
        ".claude.json",
        "policy-limits.json",
        "policy-limits.json.signature.json",
        "policy-limits.json.stamp.json",
        "remote-settings.json",
        "remote-settings.json.synced-at",
    }
)
"""Files at the top of the folder that the engine needs and that carry no content."""
KEEP_DIRS = frozenset({"plugins"})
"""Folders the engine needs: the store of its built-in plugins (metadata, no content)."""


def purge_agent_files(agent_dir: Path, clock: Clock) -> int:
    """Delete the files older than `DAYS` under `agent_dir` but the kept ones, and the folders
    left empty; return how many files went."""
    if not agent_dir.is_dir():
        return 0
    cutoff = clock.now().timestamp() - timedelta(days=DAYS).total_seconds()
    deleted = 0
    for root, dirs, names in os.walk(agent_dir, topdown=False, followlinks=False):
        folder = Path(root)
        relative = folder.relative_to(agent_dir)
        if relative.parts and relative.parts[0] in KEEP_DIRS:
            continue
        for name in names:
            if relative == Path(".") and name in KEEP:
                continue
            path = folder / name
            if path.lstat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        for name in dirs:
            child = folder / name
            if not child.is_symlink() and child.is_dir() and not any(child.iterdir()):
                if not (relative == Path(".") and name in KEEP_DIRS):
                    child.rmdir()
    logger.info("purge: agent files=%d", deleted)
    return deleted
