"""Refusals specific to a submission: creating, reading, interest and changes of state (FR-26, FR-28,
FR-32, FR-35, FR-42)."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import ClassVar

from nptc.db.models.catalogue_entry import CatalogueEntryStatus
from nptc.submissions.duplicates import DuplicateMatch
from nptc.terminology.errors import TerminologyUnavailableError, TerminologyUpstreamError

__all__ = [
    "AmendmentEntryNotActiveError",
    "AmendmentRefusal",
    "AmendmentRefusedError",
    "ClosedSubmissionInterestError",
    "CodeRefusal",
    "EditReasonRequiredError",
    "ExpectedReleaseBeforeRaisedError",
    "FreeTextField",
    "FreeTextRefusedError",
    "IllegalSubmissionTransitionError",
    "MalformedSubmissionCursorError",
    "OwnSubmissionInterestError",
    "SubmissionCodeRefusedError",
    "SubmissionCodeUnavailableError",
    "SubmissionCodeUpstreamError",
    "SubmissionDuplicatesFoundError",
    "SubmissionNotEditableError",
    "SubmissionNotFoundError",
    "SubmissionVersionConflictError",
    "TerminologyRequestFieldRequiredError",
    "TerminologyRequestNotOpenError",
    "TransitionNeedsOwnRouteError",
    "TransitionReasonRequiredError",
]


class CodeRefusal(StrEnum):
    """Why a supplied SNOMED CT code cannot be stored on a submission. A fixed set, so the API can
    name the reason to the caller without echoing anything the caller typed."""

    NOT_FOUND = "not-found"
    INACTIVE = "inactive"
    #: The server did not say whether the code is active (hazard H-05). An unresolved status is
    #: not "active", so the code is refused rather than stored on a guess.
    STATUS_NOT_REPORTED = "status-not-reported"
    #: The server knows the code but returned no fully specified name to store beside it.
    NO_FSN = "no-fsn"
    #: The submission is `Awaiting terminology`, so its code is attached by an Administrator once
    #: the authority has issued it, and no edit of the working copy sets it (FR-30).
    ATTACH_ONLY = "attach-only"


class SubmissionCodeRefusedError(ValueError):
    """The terminology server answered, and the answer rules the code out. Distinct from an
    outage, which is `nptc.terminology.errors.TerminologyUnavailableError` (503)."""

    http_status: ClassVar[int] = 422

    def __init__(self, reason: CodeRefusal) -> None:
        self.reason = reason
        super().__init__(f"SNOMED CT code refused: {reason.value}")


class SubmissionCodeUnavailableError(TerminologyUnavailableError):
    """The terminology server could not be reached while checking the SNOMED CT code on a
    submission. A subclass so the response can name the code field, which the shared
    `TerminologyUnavailableError` handler cannot know."""


class SubmissionCodeUpstreamError(TerminologyUpstreamError):
    """The terminology server answered with something unusable while checking the SNOMED CT code
    on a submission. A subclass for the same reason as `SubmissionCodeUnavailableError`."""


class FreeTextField(StrEnum):
    """The free-text fields of a submission. A fixed set, so the API can name which one was refused
    without echoing anything the caller typed."""

    NOTES = "notes"
    ORGANISATION = "organisation"
    REASON = "reason"
    BODY = "body"
    PROPOSED_FSN = "proposed_fsn"
    JUSTIFICATION = "justification"
    EXTERNAL_REFERENCE_SYSTEM = "external_reference_system"
    EXTERNAL_REFERENCE_REF = "external_reference_ref"


class FreeTextRefusedError(ValueError):
    """A free-text field carries an invisible character with no single deterministic repair, such
    as a zero-width space or a text-direction override (FR-63). The message names the field and the
    codepoints, never the characters themselves."""

    http_status: ClassVar[int] = 422

    def __init__(self, field: FreeTextField, codepoints: tuple[str, ...]) -> None:
        self.field = field
        super().__init__(f"{field.value} contains an invisible character: {', '.join(codepoints)}")


class SubmissionDuplicatesFoundError(Exception):
    """The submission matches an active catalogue entry or an open submission, and the request did
    not confirm it is a different test (FR-25). Carries the matches so the submitter can review
    them. The message names none of the submitted text."""

    http_status: ClassVar[int] = 409

    def __init__(self, matches: Sequence[DuplicateMatch]) -> None:
        self.matches = tuple(matches)
        super().__init__(f"submission matches {len(self.matches)} existing record(s)")


class AmendmentEntryNotActiveError(Exception):
    """The entry an amendment names exists but is not `active`, so it cannot be amended (FR-35). The
    status is carried so the response can name it; the message holds no caller text."""

    http_status: ClassVar[int] = 409

    def __init__(self, status: CatalogueEntryStatus) -> None:
        self.status = status
        super().__init__(f"amendment refused, entry is {status.value}")


class AmendmentRefusal(StrEnum):
    """Why an amendment proposes no change. A fixed set, so the API can name the reason without
    echoing anything the caller typed."""

    #: No synonym and no code were sent.
    NOTHING_PROPOSED = "nothing-proposed"
    #: Synonyms were sent, and the entry already has every one, and no code was sent.
    NOTHING_NEW = "nothing-new"
    #: The code is the one the entry already carries, and no synonym is left to propose.
    CODE_ALREADY_BOUND = "code-already-bound"
    #: A reviewer's edit of an amendment named a field an amendment does not propose, or left it
    #: with neither a synonym nor a code (FR-23).
    FIELD_NOT_AMENDABLE = "field-not-amendable"


class AmendmentRefusedError(ValueError):
    """The amendment would leave the entry as it is (FR-35)."""

    http_status: ClassVar[int] = 422

    def __init__(self, reason: AmendmentRefusal) -> None:
        self.reason = reason
        super().__init__(f"amendment refused: {reason.value}")


class SubmissionNotFoundError(LookupError):
    """No submission has the identifier. The message names none, so a log line never carries one."""

    http_status: ClassVar[int] = 404


class MalformedSubmissionCursorError(ValueError):
    """An `after` cursor this API did not issue; the first page would restart a paging loop."""

    http_status: ClassVar[int] = 422


class OwnSubmissionInterestError(Exception):
    """A submitter tried to register interest in their own submission (FR-32). Interest counts
    other people's backing."""

    http_status: ClassVar[int] = 409


class ClosedSubmissionInterestError(Exception):
    """The submission is closed, so its interest no longer changes (FR-32): a registration would
    back a proposal that is over, and a revocation would move the count that stood at closure. The
    message holds no caller text."""

    http_status: ClassVar[int] = 409

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__(f"interest refused, submission is {state}")


class IllegalSubmissionTransitionError(Exception):
    """The permitted-transitions table has no row for this move from the submission's current state
    (FR-28). A closed submission is never reinterpreted, so a second withdrawal lands here. The
    message holds no caller text."""

    http_status: ClassVar[int] = 409

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"transition refused, {from_state} to {to_state} is not permitted")


class SubmissionVersionConflictError(Exception):
    """The caller's `expected_row_version` is not the submission's current one, so someone else
    changed it since the caller read it (FR-28, FR-38's pattern). Carries both numbers so the
    response can tell the caller where it stands."""

    http_status: ClassVar[int] = 409

    def __init__(self, *, expected_row_version: int, current_row_version: int) -> None:
        self.expected_row_version = expected_row_version
        self.current_row_version = current_row_version
        super().__init__(
            f"submission row_version is {current_row_version}, expected {expected_row_version}"
        )


class TransitionReasonRequiredError(ValueError):
    """The reason for a change of state is empty once cleaned (FR-28). A change of state always
    says why."""

    http_status: ClassVar[int] = 422


class SubmissionNotEditableError(Exception):
    """The submission is in a state in which a reviewer may not edit its working copy (FR-23). The
    state is carried so the response can name it; the message holds no caller text."""

    http_status: ClassVar[int] = 409

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__(f"working copy edit refused, submission is {state}")


class EditReasonRequiredError(ValueError):
    """The reason for an edit of a working copy is empty once cleaned (FR-23). An edit always says
    why, as a change of state does."""

    http_status: ClassVar[int] = 422


class TransitionNeedsOwnRouteError(Exception):
    """The move is in the table, but it carries more than a reason, so the general transition route
    cannot make it (FR-30). The states are carried so the response can name them; the message holds
    no caller text."""

    http_status: ClassVar[int] = 409

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"transition refused, {from_state} to {to_state} has its own route")


class TerminologyRequestFieldRequiredError(ValueError):
    """A field of the terminology request is empty once cleaned (FR-30, FR-31). `field` names it,
    so the response says which one to fill without echoing anything the caller typed."""

    http_status: ClassVar[int] = 422

    def __init__(self, field: FreeTextField) -> None:
        self.field = field
        super().__init__(f"terminology request field is blank: {field.value}")


class TerminologyRequestNotOpenError(Exception):
    """The terminology request can be changed only while the submission is `Awaiting terminology`
    (FR-30). The state is carried so the response can name it; the message holds no caller text."""

    http_status: ClassVar[int] = 409

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__(f"terminology request change refused, submission is {state}")


class ExpectedReleaseBeforeRaisedError(ValueError):
    """The expected release of a terminology request is earlier than the day the request was
    raised (FR-31). A request cannot be due before it was asked for. The message holds no caller
    text."""

    http_status: ClassVar[int] = 422

    def __init__(self) -> None:
        super().__init__("expected release is earlier than the date the request was raised")
