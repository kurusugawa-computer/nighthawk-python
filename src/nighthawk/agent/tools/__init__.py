"""Optional capability families composed over grants.

Each ``build_*`` function returns a :class:`nighthawk.agent.Grant` whose
capabilities carry Python-bound authority boundaries and declared effect
footprints. Compose them into a run-entry grant with ``Grant.compose``. No
family is required by the architecture; they are ordinary Python compositions.
"""

from __future__ import annotations

from . import artifacts, coding_actions, inspect_workspace, requests, research_web
from ._default_backends import build_default_file_writer
from ._default_web_backends import build_default_web_fetch_client
from .artifacts import build_artifact_storage
from .coding_actions import CodingActionPolicy, build_coding_actions
from .inspect_workspace import build_inspect_workspace
from .research_web import build_research_web

__all__ = [
    "CodingActionPolicy",
    "artifacts",
    "build_artifact_storage",
    "build_coding_actions",
    "build_default_file_writer",
    "build_default_web_fetch_client",
    "build_inspect_workspace",
    "build_research_web",
    "coding_actions",
    "inspect_workspace",
    "requests",
    "research_web",
]
