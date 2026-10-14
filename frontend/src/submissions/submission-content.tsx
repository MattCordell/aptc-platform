import { Fragment } from "react";

import { useSubmissionPropertyDefinitions } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { formatDate } from "../catalogue/format-date.ts";
import { describePropertyValue } from "../catalogue/format-property-value.ts";
import { CodeChip } from "../components/code-chip.tsx";

type SubmissionDetail = components["schemas"]["SubmissionDetail"];
type SubmittedValue = components["schemas"]["SubmittedPropertyValue"];

const MUTED = "text-[var(--color-text-muted)]";
const WRAP = "[overflow-wrap:anywhere]";

function None({ children }: { children: string }) {
  return <span className={MUTED}>{children}</span>;
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

/**
 * The submitted property values, labelled with the registry's own labels. A key
 * the registry no longer lists shows as its key, and so does every key for a
 * caller who may not read the registry (an Observer holds `submission.view`
 * but not `registry.read`). Each value is read by its shape
 * (`describePropertyValue`), never by the property's datatype (FR-77).
 */
function PropertyValues({ values }: { values: SubmissionDetail["property_values"] }) {
  const keys = Object.keys(values).filter((key) => values[key].length > 0);
  if (keys.length === 0) {
    return null;
  }
  return <LabelledPropertyValues values={values} keys={keys} />;
}

/** Split from `PropertyValues` so the definitions are asked for only when
 * there is a value to label. */
function LabelledPropertyValues({
  values,
  keys,
}: {
  values: SubmissionDetail["property_values"];
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
              {values[key].length === 1 ? (
                <PropertyValueView item={values[key][0]} />
              ) : (
                <ul className="m-0 flex list-none flex-col gap-2 p-0">
                  {values[key].map((item, index) => (
                    <li key={index}>
                      <PropertyValueView item={item} />
                    </li>
                  ))}
                </ul>
              )}
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

function ReferenceCheck({ submission }: { submission: SubmissionDetail }) {
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

/**
 * What the submitter sent (FR-26, FR-27, FR-34). For a new test every part is a
 * proposal. For an amendment only the other names and the code are: the
 * heading's term is the entry's own term when the change was proposed, so the
 * page says so rather than let it read as a rename.
 */
export function SubmissionContent({ submission }: { submission: SubmissionDetail }) {
  const isAmendment = submission.kind === "amendment";
  return (
    <>
      <EntrySection title={isAmendment ? "Proposed change" : "Proposed test"}>
        {isAmendment ? (
          <p className="m-0">
            The heading shows the entry&rsquo;s term when this change was proposed. It is
            not a proposed new term.
          </p>
        ) : null}
        <dl className="m-0 grid grid-cols-1 gap-y-1">
          <dt className="font-medium">Other names</dt>
          <dd className="m-0 mb-3 min-w-0">
            {submission.synonyms.length === 0 ? (
              <None>None proposed</None>
            ) : (
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {submission.synonyms.map((name) => (
                  <li key={name} className={WRAP}>
                    {name}
                  </li>
                ))}
              </ul>
            )}
          </dd>

          <dt className="font-medium">SNOMED CT code</dt>
          <dd className="m-0 min-w-0">
            {submission.snomed_code === null ? (
              <None>None proposed</None>
            ) : (
              <span className="flex flex-col gap-1">
                <span>
                  <CodeChip code={submission.snomed_code} />
                </span>
                {submission.snomed_fsn === null ? null : (
                  <span className={WRAP}>{submission.snomed_fsn}</span>
                )}
              </span>
            )}
          </dd>
        </dl>
      </EntrySection>

      <PropertyValues values={submission.property_values} />

      <EntrySection title="Notes">
        {submission.notes === null ? (
          <p className="m-0">No notes were added.</p>
        ) : (
          <p className={`m-0 whitespace-pre-line ${WRAP}`}>{submission.notes}</p>
        )}
      </EntrySection>

      <EntrySection title="Supporting reference">
        <ReferenceCheck submission={submission} />
      </EntrySection>
    </>
  );
}
