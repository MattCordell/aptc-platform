# Editing a submission

During triage, a reviewer can change the content of a submission before it is approved. This page
explains who can edit, what you can change, how to save, and how the page shows your changes. You
must be signed in as a Reviewer or an Administrator.

## What an edit does

Every submission keeps what the submitter sent. When you edit, the platform keeps your changes in
a separate **working copy**. The submitter's original is never changed. When an Administrator
approves the submission, the platform writes the working copy to the catalogue. Until then, the
page shows both.

Only Reviewers and Administrators see the working copy. Other people see what the submitter sent.

## Open the form

Open a submission from the Submissions page. See
[Reading a submission, registering interest and withdrawing](reading-a-submission.md). In the
column beside the submission, the **Edit** panel has an **Edit** button. The panel appears only
when both of these are true:

1. You may review submissions.
2. The submission is in Triage, Under review or Ready for approval.

In any other state the platform refuses an edit, so the page offers none. A submission that is
Submitted has not been picked up yet. Start triage first. See
[Triaging a submission](triaging-a-submission.md).

Choose **Edit**. The form opens in place of the submission's content, and focus moves to its first
field. The form starts with the content as it stands now, which is the working copy if there is
one, and otherwise what the submitter sent.

## What you can change

For a **new test**, the form has:

- **Test name**. It is required.
- **Other names**. Press Enter or type a semicolon to add a name. Choose **Remove** beside a name
  to take it out.
- **SNOMED CT code**. The form shows the code the submission holds now. To change it, search for
  a procedure under **Choose a different SNOMED CT code** and pick one. The platform names the
  code before you can save. Choose **Keep the current code** to go back.
- **Properties**. The same properties as the form for a new test.
- **Notes**. Empty the box to remove the note.
- **Reason**. It is required, and spaces alone do not count.

For an **amendment**, the form has only the other names, the SNOMED CT code and the reason,
because an amendment proposes nothing else. An amendment must keep at least one other name or a
code.

You cannot remove a code once a submission holds one. You can only pick a different code. You
cannot change the reference link or the organisation.

## Save

1. Change the fields you want.
2. Write a reason.
3. Choose **Save**.

The form sends only the fields you changed. This protects other people's work: if another reviewer
changed the notes while your form was open, and you did not touch the notes, your save does not
overwrite them. If you changed nothing, the form says so and sends nothing.

After a save, the form closes and focus moves to a line such as "Saved. Changed: Test name, Notes."
The page now shows your working copy. Choose **Cancel** to close the form without saving.

## Where the reason goes

The reason goes to the audit log. It does **not** appear in the submission's **History**, because
**History** lists only changes of state. The submitter cannot read it.

## How the page shows a change

The heading keeps the submitter's term, so the submission is always easy to recognise. In the
main column, each field you changed shows your value. Beneath it, a line reads **Changed from
the original:** followed by what the submitter sent. A field you did not change shows nothing
extra. The words carry the meaning, so the page does not depend on colour. A note at the top of
the section says a reviewer changed some of the content.

A field you emptied, such as a note or a property, shows "None" with the original beneath it.

When you approve a new test, the **Approve** box names the term you set. It still opens with the
submission's heading term, so you can see which submission you are approving.

## When the platform refuses

The form stays open, and your changes and reason stay in it:

- **A field is not accepted.** The field is marked with the platform's explanation. For a
  property, the property's row is marked. Choose a link in the error summary to jump to the field.
- **The reason is not accepted.** The Reason field is marked, for example when it holds a
  character that cannot be shown.
- **The submission changed since you opened the form.** Someone else changed or moved it. The page
  reads the latest version, and the form says your changes are kept. Choose **Cancel** to read
  the latest version first, or choose **Save** again to apply your changes on top of it. Only the
  fields you changed are sent.
- **The submission can no longer be edited.** Another reviewer has moved it to a state that does
  not allow edits, such as Approved. **Save** stops working, and the form explains why. Your text
  stays so you can copy it. Choose **Cancel** to close the form.
- **You may not edit.** The platform's own sentence appears. A page that was open when your role
  changed can still show the **Edit** button.

If you cannot tell why an edit is refused, ask an Administrator.
