"""Refusals specific to internal comments (FR-95)."""

from __future__ import annotations

from typing import ClassVar

from aptc.submissions.errors import FreeTextField

__all__ = ["CommentAlreadyWithdrawnError", "CommentNotFoundError", "CommentTextRequiredError"]


class CommentNotFoundError(LookupError):
    """No comment has the identifier on the target named in the request. A comment that exists on a
    different target is refused the same way, so a caller learns nothing about where comments are.
    The message names no identifier."""

    http_status: ClassVar[int] = 404


class CommentAlreadyWithdrawnError(Exception):
    """The comment was withdrawn before, and a withdrawal is never overwritten, so the first reason
    and time stay as they were. The message holds no caller text."""

    http_status: ClassVar[int] = 409


class CommentTextRequiredError(ValueError):
    """A comment body or a withdrawal reason is empty once cleaned. `field` names which, from a fixed
    set, so the message holds no caller text."""

    http_status: ClassVar[int] = 422

    def __init__(self, field: FreeTextField) -> None:
        self.field = field
        super().__init__(f"{field.value} is empty")
