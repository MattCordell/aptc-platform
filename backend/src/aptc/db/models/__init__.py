"""Import-aggregator so ``Base.metadata`` is complete for Alembic autogenerate.

Every model module must be imported here for its side effect of registering
its table with ``aptc.db.base.Base.metadata`` - a model that exists but
isn't imported through this package is invisible to autogenerate and to
``compare_metadata()``.
"""

from aptc.db.models.audit import AuditEvent
from aptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from aptc.db.models.code_binding import CodeBinding, CodeBindingEditionHint, CodeBindingStatus
from aptc.db.models.designation import Designation, DesignationStatus
from aptc.db.models.designation_collision_acknowledgement import (
    DesignationCollisionAcknowledgement,
)
from aptc.db.models.entry_seed_provenance import EntrySeedProvenance
from aptc.db.models.internal_comment import InternalComment
from aptc.db.models.local_code import LocalCode, LocalCodeStatus
from aptc.db.models.local_code_snomed_map import LocalCodeSnomedMap, SnomedMapMatchStrength
from aptc.db.models.local_code_system import LocalCodeSystem, LocalCodeSystemStatus
from aptc.db.models.property_definition import (
    BindingStrength,
    BindingTarget,
    PropertyCardinality,
    PropertyDefinition,
    PropertyOrigin,
    PropertyScope,
    PropertyStatus,
)
from aptc.db.models.property_value import PropertyValue
from aptc.db.models.seed_import import SeedImport
from aptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from aptc.db.models.submission_interest import SubmissionInterest
from aptc.db.models.submission_terminology_request import SubmissionTerminologyRequest
from aptc.db.models.submission_transition import SubmissionTransition
from aptc.db.models.submission_working_copy import SubmissionWorkingCopy
from aptc.db.models.terms_acceptance import TermsAcceptance
from aptc.db.models.user import User, UserStatus
from aptc.db.models.user_identity import UserIdentity
from aptc.db.models.user_role import UserRole
from aptc.db.models.validation_finding import ValidationFinding, ValidationFindingStatus

__all__ = [
    "AuditEvent",
    "BindingStrength",
    "BindingTarget",
    "CatalogueEntry",
    "CatalogueEntryStatus",
    "CodeBinding",
    "CodeBindingEditionHint",
    "CodeBindingStatus",
    "Designation",
    "DesignationCollisionAcknowledgement",
    "DesignationStatus",
    "EntrySeedProvenance",
    "InternalComment",
    "LocalCode",
    "LocalCodeSnomedMap",
    "LocalCodeStatus",
    "LocalCodeSystem",
    "LocalCodeSystemStatus",
    "PropertyCardinality",
    "PropertyDefinition",
    "PropertyOrigin",
    "PropertyScope",
    "PropertyStatus",
    "PropertyValue",
    "SeedImport",
    "SnomedMapMatchStrength",
    "Submission",
    "SubmissionInterest",
    "SubmissionKind",
    "SubmissionState",
    "SubmissionTerminologyRequest",
    "SubmissionTransition",
    "SubmissionWorkingCopy",
    "TermsAcceptance",
    "User",
    "UserIdentity",
    "UserRole",
    "UserStatus",
    "ValidationFinding",
    "ValidationFindingStatus",
]
