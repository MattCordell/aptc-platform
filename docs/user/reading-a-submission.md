# Reading a submission, registering interest and withdrawing

A submission is a proposal for a new test, or for a change to an existing one. This page
explains what the page for one submission shows, how you register interest in it, how
reviewers see who registered, and how a submitter withdraws their own submission. You must be
signed in to open it.

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
  justification. This section is absent when the submission has none. Each property shows its
  label. An Observer may not read the property registry, so an Observer sees the property's
  key instead, for example `assay_method`.
- **Notes**: any free text the submitter added.
- **Supporting reference**: the link the submitter gave. Below it, the page says when the
  server checked the link and which HTTP status the page answered with. It says so plainly
  when there is no reference, or when the link has not been checked.

The reference link opens the submitter's own page. The check shows that the page answered,
not that it supports the proposal. Some sites refuse automated visits and answer with status
401 or 403. The platform accepts those two answers, so you may see one of them.

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

## See who registered interest

Reviewers and Administrators see an extra panel, **Who registered interest**. Other people
do not see it, and their browser never asks the platform for it. The panel shows:

- A summary, for example "3 people from 2 organisations". If no one gave an organisation, it
  reads "1 person. No one gave an organisation."
- **Organisations**: each organisation with the number of people who named it. Spellings that
  differ only by capital letters or spaces count as one organisation. People who gave no
  organisation are grouped under "No organisation given", and that group is not counted in the
  number of organisations.
- **People**: each person's name and username, the organisation they gave when they registered,
  and the date they registered. The newest registration comes first. The organisation is the
  one recorded at registration, so it can differ from the person's profile today. A closed
  account shows the placeholder name the platform sends.

The panel shows the people who hold interest now. When someone withdraws their interest, they
leave the list. The panel shows for submissions in any state, including closed ones.

## Withdraw your own submission

If you sent a submission and it is still open, you can take it back. The **Your submission**
panel shows a **Withdraw submission** button only when all three of these are true:

1. The submission is yours.
2. It is open: its state is Submitted or Triage.
3. Your role may withdraw a submission. Provisional users, Members and Reviewers may.

To withdraw:

1. Choose **Withdraw submission**.
2. Write a reason. It is required, and spaces alone do not count. The platform records it in
   the audit log with the change. It is not published.
3. Choose **Confirm withdrawal**.

The state changes to Withdrawn at once, without reloading the page, and focus moves to the
line "You withdrew this submission." A withdrawn submission stays on the platform, and
people who can open submissions can still read it, but it cannot be reopened. To propose it
again, send a new submission.

Choose **Cancel** to close the box without withdrawing.

## When the platform refuses

The page can be a little out of date, for example if a reviewer closed the submission while
you had it open. If the platform refuses your request, the page shows its reason beneath the
button and reloads the submission. The button then disappears or changes to match.

If you try to withdraw and the platform refuses, the box stays open and shows why:

- **The submission changed since you opened it.** The page reads the new version at once,
  and the box tells you so. Check what the page now shows, then choose **Confirm withdrawal**
  again. Your reason stays in the box.
- **The submission is already closed.** The platform's own sentence appears, and **Confirm
  withdrawal** no longer works. Choose **Cancel**. The button has gone because the page now
  shows the closed state.
- **The reason is not accepted.** The Reason field is marked with the platform's explanation,
  for example when the reason holds a character that cannot be shown.

If a refresh fails, a line at the top says the submission may be out of date. The submission
already on screen stays visible. If the first load fails, choose **Try again**.
