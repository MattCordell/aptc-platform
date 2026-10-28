"""Internal comments: staff-only notes on a submission or a catalogue entry (FR-95).

`aptc.comments.service` posts, lists and withdraws them. They are append-only, and nothing about them
may reach a submitter or the public (`aptc.db.models.internal_comment`).
"""
