# Finding submissions

A submission is a proposal for a new test, or for a change to an existing one. The
Submissions page lists them, newest first. This page explains how to find the one you want.
You must be signed in to open it.

## Open the list

Choose **Submissions** in the top menu, or go to `/submissions`. To propose something new,
choose **Create submission**. See [Submitting a new test](submitting-a-new-test.md).

## Choose a view

Two links under the page title switch the view. The current one is underlined.

- **All submissions** lists submissions from everyone. It shows open submissions unless you
  choose another state.
- **My submissions** lists only your own, in every state. A submission you withdrew stays on
  this list.

Each view has its own address. `/submissions?mine=true` is **My submissions**.

## Filter by state

The **State** list narrows the view to one kind of submission:

- **Open** shows submissions still waiting for a decision: Submitted and Triage.
- **All states** shows every submission, including closed ones.
- **Submitted**, **Triage**, **Published in release**, **Rejected** or **Withdrawn** shows
  that state alone.

**All submissions** starts on **Open**. **My submissions** starts on **All states**.

The filter is part of the address, for example `/submissions?state=Withdrawn`. You can
bookmark a filtered view or send the link to someone. Choosing a view's starting state again
returns the address to its plain form.

## Read a row

Each row shows:

- **Requesting term**, the proposed name. Choose it to open the submission. See
  [Reading a submission, registering interest and withdrawing](reading-a-submission.md).
- **Kind**. A new test says "New test". A change says "Amendment to" and the identifier of
  the entry it changes, for example NPTC-000247. Choose the identifier to open that entry.
- **State**, written out in words as well as shown in colour.
- **Submitted**, the date, in your own time zone.
- **Submitter**. Most people see a username. Reviewers and Administrators see more. See
  [the Details panel](reading-a-submission.md#the-details-panel).
- **Interest**, the number of people who registered interest. **Not shown** means your
  account may not see counts, not that the count is zero. See
  [Interest](reading-a-submission.md#interest).

## Move through a long list

The page shows fifty submissions at a time. Below the list:

- Choose **Next page** to see older submissions.
- Choose **Previous page** to go back. It is unavailable on the first page you opened.
- **No more results** means you have reached the oldest submission.

The address changes with each page, so a link to page two opens page two. Changing the view
or the state returns you to the first page.

You can use the keyboard throughout. Tab to a link, button or the **State** list and press
Enter, or use the arrow keys in the list.

## If something goes wrong

- **There are no open submissions.** Nothing matches the view and state you chose. The
  message names the state when you have filtered by one.
- **You have no submissions yet.** You have not submitted anything. Choose **Create
  submission** to start.
- **The submissions could not be loaded.** Try again in a moment, or change the state.
- **The list could not be refreshed just now.** The page keeps the submissions already on
  screen and warns you they may be out of date.
- A message that your account may not view submissions. The platform refused the request.
  Ask an administrator about your access.

There is no separate "My interest" page. The menu no longer links to one, and the old
address shows the "We couldn't find that page" screen.
