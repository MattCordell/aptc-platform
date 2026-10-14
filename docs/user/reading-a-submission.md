# Reading a submission and registering interest

A submission is a proposal for a new test, or for a change to an existing one. This page
explains what the page for one submission shows, and how you register interest in it. You must
be signed in to open it.

## Open a submission

On the Submissions page, choose a **Requesting term**. See
[Finding submissions](finding-submissions.md). The page has its own address,
`/submissions/` followed by the submission's identifier. If the identifier is unknown or
malformed, you see "We couldn't find that page".

## What the page shows

The heading is the submission's term. The main column shows what the submitter sent:

- **Other names**: the extra names the submitter proposes. It reads "None proposed" when
  there are none.
- **SNOMED CT code**: the proposed code and its fully specified name, as the terminology
  server returned it. It reads "None proposed" when there is no code.
- **Proposed properties**: each property the submitter filled in, with its value and any
  justification. This section is absent when the submission has none.
- **Notes**: any free text the submitter added.
- **Supporting reference**: the link the submitter gave. Below it, the page says when the
  server checked the link and which HTTP status the page answered with. It says so plainly
  when there is no reference, or when the link has not been checked.

The reference link opens the submitter's own page. The platform has checked that the page
answered, not that it supports the proposal.

## A change to an existing test

For a change, the section is called **Proposed change**, not **Proposed test**. Only the other
names and the code are proposals. The heading shows the entry's own term as it stood when the
change was proposed. It is not a proposed new term, and the page says so.
The **Kind** line in the **Details** panel names the entry, for example NPTC-000247. Choose
the identifier to open that entry.

## The Details panel

Beside the main column, **Details** shows:

- **Kind**: New test, or Amendment to an entry.
- **State**: Submitted, Triage, Published in release, Rejected or Withdrawn. Submitted and
  Triage are the open states. The state is written out in words as well as shown in colour.
- **Submitted**: the date, in your own time zone.
- **Submitter**: most people see a username. Reviewers and Administrators also see the
  submitter's name and the organisation they gave with the submission. The page shows only
  what the platform sent it.

## Interest

**Interest** shows how many people have registered interest in the submission. Interest tells
reviewers how many people would use the test. It helps them set priority. It does not decide
whether a submission is approved, and the page says so.

If your account may not see counts, the panel says the number is not shown to you. That does
not mean the count is zero.

## Register or withdraw your interest

The **Register interest** button appears only when all three of these are true:

1. Your role may register interest. Members, Reviewers and Administrators may. Provisional
   users and Observers may not, so they see the count but no button.
2. The submission is not your own.
3. The submission is open: its state is Submitted or Triage.

To register, choose **Register interest**. The button then reads **Withdraw interest**.
Choose it to withdraw. The count changes at once, without reloading the page, and a screen
reader announces the new state and the count.

Registering twice, or withdrawing twice, changes nothing the second time.

## When the platform refuses

The page can be a little out of date, for example if a reviewer closed the submission while
you had it open. If the platform refuses your request, the page shows its reason beneath the
button and reloads the submission. The button then disappears or changes to match.

If a refresh fails, a line at the top says the submission may be out of date. The submission
already on screen stays visible. If the first load fails, choose **Try again**.
