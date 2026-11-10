"""Version information shown to the user (window title, log).

``__version__`` in ``kipida/__init__.py`` is the single source of the release
number. When running from a git checkout (e.g. the plugins folder links to the
repository), the exact commit is appended as a PEP 440 local version, e.g.
``0.5.1+g6fc1a1e``. The commit is read directly from the ``.git`` folder: the
``git`` executable may not be on PATH inside KiCad's plugin environment.
"""
from pathlib import Path

from kipida import __version__

EDITION = "PySide6"
APP_NAME = "Ki-PIDA"

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_git_commit(repo_root: Path = _REPO_ROOT) -> str | None:
    """Return the full commit hash of HEAD, or None if not a git checkout."""
    git_dir = repo_root / ".git"
    try:
        if git_dir.is_file():  # worktree/submodule: ".git" file points elsewhere
            content = git_dir.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir:"):
                git_dir = (repo_root / content[len("gitdir:"):].strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not head.startswith("ref:"):
        return head or None  # detached HEAD holds the hash directly

    ref = head[len("ref:"):].strip()
    try:
        return (git_dir / ref).read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    try:  # ref may only exist in packed-refs
        for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    except OSError:
        pass
    return None


def get_full_version() -> str:
    """Release version plus short commit when available, e.g. '0.5.1+g6fc1a1e'."""
    commit = _read_git_commit()
    if commit:
        return f"{__version__}+g{commit[:7]}"
    return __version__


def get_window_title(subtitle: str = "Power Integrity Analyzer") -> str:
    """e.g. 'Ki-PIDA (PySide6 edition) v0.5.1+g6fc1a1e - Power Integrity Analyzer'."""
    return f"{APP_NAME} ({EDITION} edition) v{get_full_version()} - {subtitle}"
