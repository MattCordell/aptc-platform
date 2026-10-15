"""Import-aggregator so ``Base.metadata`` is complete for Alembic autogenerate.

Every model module must be imported here for its side effect of registering
its table with ``nptc.db.base.Base.metadata`` - a model that exists but
isn't imported through this package is invisible to autogenerate and to
``compare_metadata()``.
"""

from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.code_binding import CodeBinding, CodeBindingEditionHint, CodeBindingStatus
from nptc.db.models.designation import Designation, DesignationStatus
from nptc.db.models.designation_collision_acknowledgement import (
    DesignationCollisionAcknowledgement,
)
from nptc.db.models.entry_seed_provenance import EntrySeedProvenance
from nptc.db.models.internal_comment import InternalComment
from nptc.db.models.local_code import LocalCode, LocalCodeStatus
from nptc.db.models.local_code_snomed_map import LocalCodeSnomedMap, SnomedMapMatchStrength
from nptc.db.models.local_code_system import LocalCodeSystem, LocalCodeSystemStatus
from nptc.db.models.property_definition import (
    BindingStrength,
    BindingTarget,
    PropertyCardinality,
    PropertyDefinition,
    PropertyOrigin,
    PropertyScope,
    PropertyStatus,
)
from nptc.db.models.property_value import PropertyValue
from nptc.db.models.seed_import import SeedImport
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.db.models.submission_interest import SubmissionInterest
from nptc.db.models.submission_transition import SubmissionTransition
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy
from nptc.db.models.terms_acceptance import TermsAcceptance
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_identity import UserIdentity
from nptc.db.models.user_role import UserRole
from nptc.db.models.validation_finding import ValidationFinding, ValidationFindingStatus

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
