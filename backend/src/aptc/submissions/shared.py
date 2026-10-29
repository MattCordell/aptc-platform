"""Checks every kind of submission runs the same way (FR-26, FR-63, FR-82).

A new test and an amendment both take free text and an optional SNOMED CT code. One home for each
check means the two kinds cannot drift apart: a note that one accepts is a note the other accepts,
and a code the terminology server rules out is ruled out for both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from aptc.catalogue.property_values import (
    PropertyDefinitionNotFoundError,
    PropertyValidationError,
    PropertyValueInput,
    PropertyWriteIssue,
    check_property_values,
)
from aptc.db.models.property_definition import PropertyDefinition, PropertyScope, PropertyStatus
from aptc.registry.definitions import DeprecatedPropertyWriteError
from aptc.registry.handlers import DatatypeRegistry
from aptc.submissions.errors import (
    CodeRefusal,
    FreeTextField,
    FreeTextRefusedError,
    SubmissionCodeRefusedError,
    SubmissionCodeUnavailableError,
    SubmissionCodeUpstreamError,
)
from aptc.submissions.terms import SubmissionCodeMalformedError
from aptc.terminology.concepts import resolve_concept
from aptc.terminology.errors import (
    ConceptNotFoundError,
    TerminologyUnavailableError,
    TerminologyUpstreamError,
)
from aptc_shared.sctid import InvalidSCTIDError
from aptc_shared.terminology import TerminologyClient
from aptc_shared.text import clean_text

__all__ = [
    "SUBMISSION_SCOPES",
    "check_submission_property_values",
    "clean_free_text",
    "resolve_code",
]

#: A submission accepts the properties whose scope reaches it. `maintenance` properties belong to
#: the published catalogue alone (FR-09).
SUBMISSION_SCOPES = frozenset({PropertyScope.SUBMISSION, PropertyScope.BOTH})


#: Line breaks and tabs are formatting in a multi-line note, not a defect.
_NOTE_FORMATTING = frozenset({chr(10), chr(13), chr(9)})


def clean_free_text(text: str | None, *, field: FreeTextField, multiline: bool) -> str | None:
    """`text` normalised like a term, or `None` if nothing is left. Refuses any invisible
    character, except the formatting a multi-line note may carry."""
    if text is None:
        return None
    allowed = _NOTE_FORMATTING if multiline else frozenset()
    normalised, refused = clean_text(text, allowed_invisible=allowed)
    if refused:
        raise FreeTextRefusedError(field, refused)
    return normalised or None


def resolve_code(client: TerminologyClient, code: str) -> tuple[str, str]:
    """The code and the FSN the server returned for it, or a refusal. The edition's own answer
    decides: an inactive or status-less concept is refused, never stored on a guess."""
    try:
        concept = resolve_concept(client, code)
    except ConceptNotFoundError:
        raise SubmissionCodeRefusedError(CodeRefusal.NOT_FOUND) from None
    except InvalidSCTIDError as malformed:
        raise SubmissionCodeMalformedError(str(malformed)) from None
    except TerminologyUpstreamError as unusable:
        raise SubmissionCodeUpstreamError(str(unusable)) from unusable
    except TerminologyUnavailableError as unavailable:
        raise SubmissionCodeUnavailableError(
            str(unavailable), retry_after=unavailable.retry_after
        ) from unavailable
    if concept.active is None:
        raise SubmissionCodeRefusedError(CodeRefusal.STATUS_NOT_REPORTED)
    if not concept.active:
        raise SubmissionCodeRefusedError(CodeRefusal.INACTIVE)
    if concept.fsn is None or not concept.fsn.strip():
        raise SubmissionCodeRefusedError(CodeRefusal.NO_FSN)
    return concept.code, concept.fsn


def check_submission_property_values(
    session: Session,
    registry: DatatypeRegistry,
    property_values: Mapping[str, Sequence[PropertyValueInput]],
) -> None:
    """Raises one `PropertyValidationError` naming every problem: a key that is unknown,
    deprecated or out of scope, a value the registry's handler refuses, and a property the
    registry requires at submission (FR-24) that the request leaves out."""
    issues: list[PropertyWriteIssue] = []
    for key, items in property_values.items():
        try:
            check = check_property_values(session, key, items, registry, scopes=SUBMISSION_SCOPES)
        except PropertyDefinitionNotFoundError:
            issues.append(
                PropertyWriteIssue(
                    property_key=key,
                    label=key,
                    code="unknown-property",
                    message=f"{key} is not a known property",
                )
            )
        except DeprecatedPropertyWriteError:
            issues.append(
                PropertyWriteIssue(
                    property_key=key,
                    label=key,
                    code="deprecated-property",
                    message=f"{key} is deprecated and accepts no new values",
                )
            )
        else:
            issues.extend(check.issues)

    required = session.execute(
        select(PropertyDefinition)
        .where(
            PropertyDefinition.required_for_submission.is_(True),
            PropertyDefinition.status == PropertyStatus.ACTIVE,
            PropertyDefinition.scope.in_(SUBMISSION_SCOPES),
        )
        .order_by(PropertyDefinition.key)
    ).scalars()
    issues.extend(
        PropertyWriteIssue(
            property_key=definition.key,
            label=definition.label,
            code="required-property-missing",
            message=f"{definition.label} is required for a submission and has no value",
        )
        for definition in required
        if definition.key not in property_values
    )

    if issues:
        raise PropertyValidationError(tuple(issues))
