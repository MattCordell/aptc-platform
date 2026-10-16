# Triaging a submission

Triage is how a reviewer moves a submission through the working group's process and tells the
submitter why. This page explains which moves the **Triage** panel offers, who may make them,
what you write, and what the page does when the platform refuses a move. You must be signed in
as a Reviewer or an Administrator to see the panel.

## Open the Triage panel

Open a submission from the Submissions page. See
[Reading a submission, registering interest and withdrawing](reading-a-submission.md). The
**Triage** panel is in the column beside the submission. It has one button for each move the
platform allows you to make now. If it allows none, the panel is not shown.

The panel shows only moves that need a reason and nothing else. The platform decides which moves
you may make, from the submission's state and your permissions, so two people can see different
buttons on the same submission.

## The moves

| From | Button | To |
|---|---|---|
| Submitted | **Start triage** | Triage |
| Triage | **Start review** | Under review |
| Triage | **Reject** | Rejected |
| Under review | **Reject** | Rejected |
| Ready for approval | **Reject** | Rejected |
| Rejected | **Reopen** | Triage |
| Under review | **Mark ready for approval** | Ready for approval |
| Awaiting terminology | **Resume review** | Under review |

**Reject** is available from Triage, Under review and Ready for approval, so no state before
approval is a dead end. A rejected submission can be reopened to Triage, and it keeps its history.

**Resume review** is for a request for a SNOMED CT code that the authority declined, or that
nobody is pursuing. The record of the request stays.

Some moves are not on the panel because they need more than a reason: asking for a new SNOMED CT
code, attaching that code, and approving a submission. Each will have its own form. Withdrawing
a submission is also separate. See
[Withdraw your own submission](reading-a-submission.md#withdraw-your-own-submission).

## Make a move

1. Choose the button for the move.
2. Write a reason. It is required, and spaces alone do not count.
3. Choose **Confirm move**.

The state changes at once, without reloading the page. Focus moves to a line such as "Moved to
Under review.", and the panel then shows the moves that are now possible. The move appears at the
bottom of **History**.

Choose **Cancel** to close the box without moving the submission.

## Who reads the reason

The reason is not private. The platform records it in the audit log, and **History** shows it to
everyone who can open the submission, including the submitter. Write it as a note to the
submitter and to the next reviewer. Internal comments for reviewers only are a separate feature
and are not part of this panel.

## When the platform refuses

The box stays open and shows why:

- **The submission changed since you opened it.** Someone else moved it, or the page was out
  of date. The page reads the new version at once. Check the state and the buttons, then choose
  **Confirm move** again. Your reason stays in the box.
- **The move is no longer allowed.** The platform's own sentence appears, for example when
  another reviewer has already moved the submission. **Confirm move** stops working when the
  platform no longer offers the move. Choose **Cancel**.
- **You may not make this move.** The platform's own sentence appears. A page that was open
  when your role changed can still show a button that the platform now refuses.
- **The reason is not accepted.** The Reason field is marked with the platform's explanation,
  for example when the reason holds a character that cannot be shown.

If you cannot tell why a move is refused, ask an Administrator.
