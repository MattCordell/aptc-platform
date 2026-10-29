"""Append-only audit log, hash chain, and field-level diffing.

- `aptc.audit.hashing`: the SHA-256 digest each `audit_event` row carries (NFR-10).
- `aptc.audit.writer`: `append_audit_event`, the only sanctioned way to append a row (NFR-08).
- `aptc.audit.verification`: `verify_chain` walks a chain and reports the first break.
- `aptc.audit.serialisation`: strict normalisation of a value into JSON-safe form, the
  counterpart to `hashing`'s total normalisation.
- `aptc.audit.policy`: which columns of a mapped model may appear in a diff (NFR-26).
- `aptc.audit.diffing`: a `FieldDiff` from a mapped instance's attribute history or from a
  pair of snapshots.
- `aptc.audit.recording`: the entry point domain code calls (`record_change`,
  `record_snapshot_change`, `record_batch_summary`).
- `aptc.audit.queries`: the NFR-12 administrator read model, consumed by
  `aptc.api.routers.audit`.

Designs and rejected alternatives: ADR-0017 (chain), ADR-0018 (diffing), ADR-0039 (read and
export). The operator CLI that wraps `verify_chain` is `scripts/verify_audit_chain.py`; see
`docs/operations/runbooks/verify-audit-chain.md`.
"""
