"""Internal comments: staff-only notes on a submission or a catalogue entry (FR-95).

`nptc.comments.service` posts, lists and withdraws them. They are append-only, and nothing about them
may reach a submitter or the public (`nptc.db.models.internal_comment`).
"""
