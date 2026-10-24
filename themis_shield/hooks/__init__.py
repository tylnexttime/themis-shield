"""Claude Code hook clients.

Importing this package does not construct a router, load models or open a vault.
Only the running server imports hooks.service.
"""

from .claude_code_hooks import (
    message_display,
    post_tool_use,
    pre_tool_use,
    session_start,
    stop,
    user_prompt_submit,
)

__all__ = [
    "user_prompt_submit", "pre_tool_use", "post_tool_use",
    "message_display", "session_start", "stop",
]
