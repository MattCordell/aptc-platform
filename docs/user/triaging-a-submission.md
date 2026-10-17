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

Most moves need a reason and nothing else. Two moves have a form of their own, because they need
more: asking for a new SNOMED CT code and attaching that code. The platform decides which moves
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
| Under review | **Request a new code** | Awaiting terminology |
| Awaiting terminology | **Attach code** | Ready for approval |
| Awaiting terminology | **Resume review** | Under review |
| Ready for approval | **Approve** | Approved |

Only an Administrator sees **Approve** and **Attach code**. See
[Approve a submission](#approve-a-submission) and
[Attach the issued code](#attach-the-issued-code). **Request a new code** opens a form. See
[Request a new code](#request-a-new-code).

**Reject** is available from Triage, Under review and Ready for approval, so no state before
approval is a dead end. A rejected submission can be reopened to Triage, and it keeps its history.

**Resume review** is for a request for a SNOMED CT code that the authority declined, or that
nobody is pursuing. The record of the request stays.

To change what a submission proposes, such as a test name or a code, use the **Edit** button
above the panel. See [Editing a submission](editing-a-submission.md).

Withdrawing a submission is not on the panel. See
[Withdraw your own submission](reading-a-submission.md#withdraw-your-own-submission).

## Make a move

1. Choose the button for the move.
2. Write a reason. It is required, and spaces alone do not count.
3. Choose **Confirm move**.

The state changes at once, without reloading the page. Focus moves to a line such as "Moved to
Under review.", and the panel then shows the moves that are now possible. The move appears at the
bottom of **History**.

Choose **Cancel** to close the box without moving the submission.

## Approve a submission

Approving is the Administrator's decision. It writes to the catalogue, so it differs from the
other moves in three ways:

- A **new test** becomes a new active entry in the catalogue. If a reviewer edited the
  submission, the entry takes the working copy's content, and the box names that term. See
  [Editing a submission](editing-a-submission.md).
- An **amendment** changes the entry it names. The box shows that entry's identifier, for
  example NPTC-000247.
- The reason you write becomes the entry's changelog note. It appears in the entry's
  **History**, and the platform also records it in the audit log and in this submission's
  history. Write a note that explains the change to the next person who reads the entry.

To approve:

1. Choose **Approve**.
2. Write a reason. The platform decides whether the reason is good enough, so it may refuse a
   short or stock phrase. The box then shows the platform's explanation, and your reason stays.
3. Choose **Confirm approval**.

The state changes to Approved at once, without reloading the page. Focus moves to a line such as
"Approved. Created entry NPTC-000123." For an amendment the line reads "Approved. Amended entry"
and the identifier. Choose the identifier to open the entry. The **Kind** line in **Details**
links to the same entry, and it still does after you reload the page.

If the platform refuses, the box stays open and shows its own sentence. This happens, for
example, when the entry is no longer active, when an amendment no longer applies to the entry,
or when another reviewer has changed the submission. **Confirm approval** stops working when
the platform no longer offers approval. Choose **Cancel**.

## Request a new code

Use this when no SNOMED CT concept covers the test. It moves a submission that is Under review
to Awaiting terminology, and it records what you asked the terminology authority for.

1. Choose **Request a new code**.
2. Fill in the form. Every field is required except **Expected release**:
   - **Proposed fully specified name**: the name you ask the authority to create.
   - **Target edition**: the Australian or the International edition.
   - **Justification**: why no existing concept will do.
   - **External reference**: the **System** that tracks the request, for example a service desk,
     and the **Reference** the request has there.
   - **Date raised**: when you raised the request with the authority.
   - **Expected release**: when the authority expects to issue the code, if it has said.
   - **Reason**: why the submission waits. It is recorded like any other reason. See
     [Who reads the reason](#who-reads-the-reason).
3. Choose **Send request**.

If a required field is empty, or the expected release is earlier than the date raised, the box
marks each field and sends nothing. If the platform refuses a field, the box marks that field
with its explanation and keeps what you typed. Any other refusal appears as a sentence, as
described in [When the platform refuses](#when-the-platform-refuses).

The state changes to Awaiting terminology at once, and focus moves to the line "Moved to
Awaiting terminology."

## The Terminology request panel

While a submission is Awaiting terminology, a **Terminology request** panel shows the request:
the proposed name, the edition, the justification, the external reference, the date raised and
the expected release. Reviewers and Administrators see it. No one else does.

To change where the request is tracked, or when its code is expected, choose **Edit tracking
details**. The box offers the system, the reference and the expected release, and a reason. Leave
the expected release empty to clear it. The platform sends only what you changed. The proposed
name, edition, justification and date raised stay as you sent them.

After a save, focus moves to the line "Saved. The tracking details are updated." If you change
nothing, the box says so and sends nothing. The panel disappears when the submission leaves
Awaiting terminology. The platform keeps the record.

## Attach the issued code

Only an Administrator can attach the code, so a Reviewer sees no **Attach code** button. A
Reviewer who has the code asks an Administrator to attach it.

1. Choose **Attach code**.
2. Search for the code in **SNOMED CT code** and choose it from the list. The box shows the
   code, its fully specified name and its Australian preferred term, as the terminology server
   returned them. You never type the name.
3. Write a reason.
4. Choose **Attach code**.

The platform checks the code on the terminology server, writes the code and its name to the
working copy, and moves the submission to Ready for approval in one step. Focus moves to a line
such as "Attached 489004, Ferritin measurement (procedure). Moved to Ready for approval."
The panel then offers **Approve**.

If the platform refuses the code, the box marks the code field with its explanation. This
happens when the code is malformed, unknown to the Australian edition, or inactive. Choose
another code, or **Clear the code**. If the terminology server cannot be reached, the platform
changes nothing. Try again shortly.

## Who reads the reason

The reason is not private. **History** shows it to everyone who can open the submission,
including the submitter. See [History](reading-a-submission.md#history). Write it as a note to the
submitter and to the next reviewer. To leave a note that only reviewers can read, use the
**Internal comments** panel instead. See [Internal comments](internal-comments.md).

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
