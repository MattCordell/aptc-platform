import { Fragment, type ReactNode } from "react";

import { useSubmissionPropertyDefinitions } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { formatDate } from "../catalogue/format-date.ts";
import { describePropertyValue } from "../catalogue/format-property-value.ts";
import { CodeChip } from "../components/code-chip.tsx";
import {
  hasDifference,
  isReviewDetail,
  originalContent,
  workingCopyDiff,
} from "./working-copy-diff.ts";
import type { AnySubmissionDetail, WorkingContent } from "./working-copy-diff.ts";

type SubmittedValue = components["schemas"]["SubmittedPropertyValue"];
type PropertyValues = WorkingContent["property_values"];

const MUTED = "text-[var(--color-text-muted)]";
const WRAP = "[overflow-wrap:anywhere]";

function None({ children }: { children: string }) {
  return <span className={MUTED}>{children}</span>;
}

/**
 * Marks a field a reviewer changed, and shows what the submitter sent. It says so in
 * words, so the mark does not depend on colour (FR-23).
 */
function ChangedFrom({ children }: { children: ReactNode }) {
  return (
    <div className={`mt-1 text-sm ${MUTED}`}>
      <p className="m-0 font-medium">Changed from the original:</p>
      <div className={`min-w-0 ${WRAP}`}>{children}</div>
    </div>
  );
}

function PropertyValueView({ item }: { item: SubmittedValue }) {
  const { text, snomedCode } = describePropertyValue(item.value);
  return (
    <div className="flex flex-col gap-0.5">
      <span className="flex flex-wrap items-center gap-2">
        {text === "" ? null : <span className={WRAP}>{text}</span>}
        {snomedCode === null ? null : <CodeChip code={snomedCode} />}
      </span>
      {item.justification === null ? null : (
        <span className={`${WRAP} ${MUTED}`}>Justification: {item.justification}</span>
      )}
    </div>
  );
}

function ValueList({ items }: { items: SubmittedValue[] }) {
  if (items.length === 0) {
    return <None>None</None>;
  }
  return items.length === 1 ? (
    <PropertyValueView item={items[0]} />
  ) : (
    <ul className="m-0 flex list-none flex-col gap-2 p-0">
      {items.map((item, index) => (
        <li key={index}>
          <PropertyValueView item={item} />
        </li>
      ))}
    </ul>
  );
}

/**
 * The property values, labelled with the registry's own labels. A key
 * the registry no longer lists shows as its key, and so does every key for a
 * caller who may not read the registry (an Observer holds `submission.view`
 * but not `registry.read`). Each value is read by its shape
 * (`describePropertyValue`), never by the property's datatype (FR-77). A property
 * a reviewer emptied still shows, because its change is what the page marks.
 */
function PropertyValues({
  values,
  original,
  changed,
}: {
  values: PropertyValues;
  original: PropertyValues;
  changed: string[];
}) {
  const keys = [
    ...new Set([
      ...Object.keys(values).filter((key) => values[key].length > 0),
      ...changed,
    ]),
  ];
  if (keys.length === 0) {
    return null;
  }
  return (
    <LabelledPropertyValues
      values={values}
      original={original}
      changed={changed}
      keys={keys}
    />
  );
}

/** Split from `PropertyValues` so the definitions are asked for only when
 * there is a value to label. */
function LabelledPropertyValues({
  values,
  original,
  changed,
  keys,
}: {
  values: PropertyValues;
  original: PropertyValues;
  changed: string[];
  keys: string[];
}) {
  const definitions = useSubmissionPropertyDefinitions();
  const labels = new Map(
    (definitions.data?.items ?? []).map((definition) => [
      definition.key,
      definition.label,
    ]),
  );
  return (
    <EntrySection title="Proposed properties">
      <dl className="m-0 grid grid-cols-1 gap-y-1">
        {keys.map((key) => (
          <Fragment key={key}>
            <dt className="font-medium">{labels.get(key) ?? key}</dt>
            <dd className="m-0 mb-3 min-w-0 last:mb-0">
              <ValueList items={values[key] ?? []} />
              {changed.includes(key) ? (
                <ChangedFrom>
                  <ValueList items={original[key] ?? []} />
                </ChangedFrom>
              ) : null}
            </dd>
          </Fragment>
        ))}
      </dl>
    </EntrySection>
  );
}

/** A link only for a web address, so a stored value is never run as a script. */
function webAddress(value: string): URL | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url : null;
  } catch {
    return null;
  }
}

function ReferenceCheck({ submission }: { submission: AnySubmissionDetail }) {
  if (submission.reference_url === null) {
    return <p className="m-0">No supporting reference was given.</p>;
  }
  const url = webAddress(submission.reference_url);
  return (
    <>
      <p className={`m-0 ${WRAP}`}>
        {url === null ? (
          submission.reference_url
        ) : (
          <a
            href={url.href}
            rel="noopener noreferrer"
            className="text-[var(--color-accent)] hover:underline"
          >
            {submission.reference_url}
          </a>
        )}
      </p>
      {submission.reference_checked_at === null ? (
        <p className="m-0">The server has not checked this link.</p>
      ) : (
        <p className="m-0">
          The server checked this link on{" "}
          <time dateTime={submission.reference_checked_at}>
            {formatDate(submission.reference_checked_at)}
          </time>
          {submission.reference_status === null
            ? "."
            : `. The page answered with HTTP status ${submission.reference_status}.`}
        </p>
      )}
    </>
  );
}

function CodeView({ code, fsn }: { code: string | null; fsn: string | null }) {
  if (code === null) {
    return <None>None proposed</None>;
  }
  return (
    <span className="flex flex-col gap-1">
      <span>
        <CodeChip code={code} />
      </span>
      {fsn === null ? null : <span className={WRAP}>{fsn}</span>}
    </span>
  );
}

function NameList({ names }: { names: string[] }) {
  if (names.length === 0) {
    return <None>None proposed</None>;
  }
  return (
    <ul className="m-0 flex list-none flex-col gap-1 p-0">
      {names.map((name) => (
        <li key={name} className={WRAP}>
          {name}
        </li>
      ))}
    </ul>
  );
}

function NotesView({ notes }: { notes: string | null }) {
  return notes === null || notes.trim() === "" ? (
    <p className="m-0">No notes were added.</p>
  ) : (
    <p className={`m-0 whitespace-pre-line ${WRAP}`}>{notes}</p>
  );
}

/**
 * What the submission holds now (FR-26, FR-27, FR-34). A reviewer sees the working
 * copy once one exists, and each field it changed shows what the submitter sent
 * beneath it (FR-23). Everyone else sees what the submitter sent, because the
 * server sends them no working copy. For a new test every part is a proposal. For
 * an amendment only the other names and the code are: the heading's term is the
 * entry's own term when the change was proposed, so the page says so rather than
 * let it read as a rename.
 */
export function SubmissionContent({ submission }: { submission: AnySubmissionDetail }) {
  const isAmendment = submission.kind === "amendment";
  const original = originalContent(submission);
  const working = isReviewDetail(submission)
    ? (submission.working_copy ?? original)
    : original;
  const difference = workingCopyDiff(original, working);
  return (
    <>
      <EntrySection title={isAmendment ? "Proposed change" : "Proposed test"}>
        {isAmendment ? (
          <p className="m-0">
            The heading shows the entry&rsquo;s term when this change was proposed. It is
            not a proposed new term.
          </p>
        ) : null}
        {hasDifference(difference) ? (
          <p className="m-0">
            A reviewer changed some of this content. Each changed field shows what the
            submitter sent.
          </p>
        ) : null}
        <dl className="m-0 grid grid-cols-1 gap-y-1">
          {difference.preferredTerm ? (
            <>
              <dt className="font-medium">Test name</dt>
              <dd className="m-0 mb-3 min-w-0">
                <span className={WRAP}>{working.preferred_term}</span>
                <ChangedFrom>{original.preferred_term}</ChangedFrom>
              </dd>
            </>
          ) : null}

          <dt className="font-medium">Other names</dt>
          <dd className="m-0 mb-3 min-w-0">
            <NameList names={working.synonyms} />
            {difference.synonyms ? (
              <ChangedFrom>
                {original.synonyms.length === 0 ? "None" : original.synonyms.join(", ")}
              </ChangedFrom>
            ) : null}
          </dd>

          <dt className="font-medium">SNOMED CT code</dt>
          <dd className="m-0 min-w-0">
            <CodeView code={working.snomed_code} fsn={working.snomed_fsn} />
            {difference.code ? (
              <ChangedFrom>
                {original.snomed_code === null ? (
                  "None"
                ) : (
                  <CodeView code={original.snomed_code} fsn={original.snomed_fsn} />
                )}
              </ChangedFrom>
            ) : null}
          </dd>
        </dl>
      </EntrySection>

      <PropertyValues
        values={working.property_values}
        original={original.property_values}
        changed={difference.properties}
      />

      <EntrySection title="Notes">
        <NotesView notes={working.notes} />
        {difference.notes ? (
          <ChangedFrom>
            <NotesView notes={original.notes} />
          </ChangedFrom>
        ) : null}
      </EntrySection>

      <EntrySection title="Supporting reference">
        <ReferenceCheck submission={submission} />
      </EntrySection>
    </>
  );
}
