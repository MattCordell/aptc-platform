# Roles

What the Administrator and Reviewer roles can each do on the screens this platform has
shipped so far. The other authenticated roles (Provisional, Member) have the submission screens:
[Submitting a new test](submitting-a-new-test.md), [Proposing a change to an
entry](proposing-an-amendment.md), [Finding submissions](finding-submissions.md) and
[Reading a submission, registering interest and withdrawing](reading-a-submission.md). See
the PRD's permission matrix (§4.7) for what they will eventually reach. An Observer can read
submissions and see interest counts, but cannot register interest. A Provisional user can
read submissions and withdraw their own, but cannot register interest.
Anonymous use is self-explanatory from the public catalogue itself: browsing,
searching and viewing what has already been published needs no account and no role
beyond it.

## Administrator

RCPA-QAP staff and their delegates. Administrators hold every capability this platform has
shipped:

- **[Finding and filtering entries for editing](finding-entries.md)** and
  **[Editing an entry](editing-an-entry.md)** — the full editing surface: terms, code
  bindings, and registry properties, each with its own guide
  ([terms](editing-designations.md), [code bindings](binding-a-code.md),
  [registry properties](editing-registry-properties.md)).
- **[Bulk reclassify](bulk-reclassify.md)** — not available on the screen. The server route
  remains for API callers.
- **[Searching the audit log](searching-the-audit-log.md)** — finding who changed what and
  when, and exporting a filtered view. No other role can read the audit log.
- **[Seeing who registered interest](reading-a-submission.md#see-who-registered-interest)** —
  on a submission's page, with the organisations they gave.

- **[Triaging a submission](triaging-a-submission.md)** — moving a submission through Triage,
  review and rejection, with a reason the submitter can read.
- **[Editing a submission](editing-a-submission.md)** — changing a submission's content during
  triage, with a reason. The submitter's original stays on the page.
- **[Internal comments](internal-comments.md)** — reading, posting and withdrawing comments that
  only Reviewers and Administrators can see, on a submission and on a published entry.

An Administrator cannot withdraw a submission from the screen yet, even their own.

### Reaching each screen

Open **Admin** in the site header to reach the administration home at `/admin`. It shows one
card for each administration screen. Select a card's title to open the screen.

| Card | Screen | Status |
|---|---|---|
| Catalogue administration | `/admin/catalogue` | Built |
| Property registry | `/admin/properties` | Built |
| Audit log | `/admin/audit` | Built |
| User administration | `/admin/users` | Planned |
| Validation findings | `/admin/validation` | Planned |
| Release administration | `/admin/releases` | Planned |
| Export configuration | `/admin/exports/config` | Planned |

A planned card opens a page that says the screen has not been built yet.

If you are signed in without the Administrator role, the home shows a notice at the top and
keeps every card, because the server decides what you may do on each screen. The notice has
two wordings, chosen by whether you have finished the extra sign-in step:

- **Step finished:** "Your account does not have the administrator role." Ask an
  Administrator to grant you the role.
- **Step not finished:** "This session does not include the administrator role." The platform
  leaves the role out of your session until you finish the step, so this wording also appears
  for an Administrator who has not done it yet, and for any other signed-in user. If you are
  an Administrator, use **Verify now** in the banner at the top of the page, then reload.

**Multi-factor authentication.** Every administrative action needs a second sign-in step
beyond a password — see [Signing in](signing-in.md#multi-factor-authentication) for when
it is asked for and what happens if you decline it partway through.

Everything an Administrator does is checked server-side against a permission you hold,
never against the Administrator role by name — so a capability withheld from a role never
depends on this platform's own UI to enforce it (see each guide's own "If something goes
wrong" section for what a missing permission looks like from inside a screen).

## Reviewer

A working group member with editorial authority over the submission pipeline described in
the PRD, but **not** over the published catalogue. A Reviewer can move a submission through
Triage and review. The screens for asking for a new SNOMED CT code and for approving a
submission have not shipped yet.

A Reviewer can use every submission screen a Member can: [submit a new
test](submitting-a-new-test.md), [propose a change to an entry](proposing-an-amendment.md),
[find submissions](finding-submissions.md), and [register interest or withdraw your own
submission](reading-a-submission.md). On the screens that have shipped, a Reviewer also has
these capabilities that a Member lacks:

- **[Triaging a submission](triaging-a-submission.md)** — the **Triage** panel on a
  submission's page, with a reason the submitter can read in
  [History](reading-a-submission.md#history).
- **[Editing a submission](editing-a-submission.md)** — the **Edit** button on a submission's
  page, with a reason that goes to the audit log.
- **[Seeing who registered interest](reading-a-submission.md#see-who-registered-interest)**
  on a submission's page, with the organisations they gave.
- **[Internal comments](internal-comments.md)** — reading, posting and withdrawing comments that
  only Reviewers and Administrators can see, on a submission and on a published entry.
- **[Seeing who sent a submission](reading-a-submission.md#the-details-panel)** — the
  submitter's name and organisation, where a Member sees a username only.
- **Acknowledging a possible duplicate term.** Opening
  [Editing an entry's terms](editing-designations.md#if-something-goes-wrong), a Reviewer
  can acknowledge a warning-severity term collision (the same synonym recorded on more
  than one entry) so it stops recurring on every save of the entry that holds it — see
  that guide's "Resolving overlapping terms" section for what acknowledging does and does
  not affect.

A Reviewer can open the entry's edit page an Administrator uses, to read and comment on a
draft entry (see [Internal comments](internal-comments.md)). The page shows a Reviewer the
comments and no edit form. This is not only a screen-level lock: every save is checked
server-side against the `catalogue.edit_published` permission specifically, which the
Reviewer role does not hold (PRD §4.5's own boundary between Reviewer and Administrator).
Unlike the multi-factor step-up in [Signing in](signing-in.md#multi-factor-authentication),
there is nothing further to complete here — a Reviewer editing a published entry is a
capability the role does not carry, not a step left undone.
