import { useRef, useState } from "react";
import type { ClipboardEvent } from "react";

import { commitOnEnter } from "../catalogue/property-controls/types.ts";
import { splitSynonyms } from "../catalogue/split-synonyms.ts";
import { Field } from "../components/field.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { LiveRegion } from "../components/live-region.tsx";
import { RemovableList } from "../components/removable-list.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { FIELD_IDS, addNames } from "./form-state.ts";
import type { NameRow } from "./form-state.ts";

/**
 * The other names a new test is known by (FR-23): one box and a list of what has
 * been added. A short list to add to and remove from, not the entry form's synonym
 * editor, which exists to retire and reinstate names that are already stored.
 *
 * Enter adds the name and never submits the form. A semicolon adds the names before
 * it, and a pasted list such as `Zovirax; Cyclir` adds one name each, split the way
 * the catalogue splits a pasted synonym cell (FR-04). Text left in the box is sent
 * with the form, so a forgotten Enter loses nothing.
 */

export interface OtherNamesChange {
  names: NameRow[];
  nameDraft: string;
}

function list(terms: string[]): string {
  return terms.join(", ");
}

export function OtherNamesField({
  names,
  draft,
  error,
  onChange,
  label = "Other names",
}: {
  names: NameRow[];
  draft: string;
  error?: string;
  onChange: (change: OtherNamesChange) => void;
  label?: string;
}) {
  const { message, politeness, announce } = useAnnounce();
  const box = useRef<HTMLInputElement>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  /**
   * Adds the given names, keeps `remainder` in the box and says what happened. A
   * name that is already listed is also named under the box, because the live
   * region is for assistive technology only. When nothing was added, `kept` stays
   * in the box so the user can see what was refused.
   */
  function add(candidates: string[], remainder: string, kept = remainder) {
    const result = addNames(names, candidates);
    const nothingAdded = result.added.length === 0 && result.repeats.length > 0;
    onChange({ names: result.names, nameDraft: nothingAdded ? kept : remainder });
    const repeated =
      result.repeats.length === 0
        ? null
        : `${list(result.repeats)} ${result.repeats.length === 1 ? "is" : "are"} already in the ${label} list.`;
    setRefusal(repeated);
    const said: string[] = [];
    if (result.added.length > 0) {
      said.push(
        `Added ${list(result.added)}. ${result.names.length} in the ${label} list.`,
      );
    }
    if (repeated !== null) {
      said.push(repeated);
    }
    if (said.length > 0) {
      announce(said.join(" "));
    }
  }

  function type(text: string) {
    setRefusal(null);
    if (!text.includes(";")) {
      onChange({ names, nameDraft: text });
      return;
    }
    const parts = text.split(";");
    const remainder = (parts.pop() ?? "").trimStart();
    add(parts, remainder);
  }

  function paste(event: ClipboardEvent<HTMLInputElement>) {
    const parts = splitSynonyms(event.clipboardData.getData("text"));
    if (parts.length < 2) {
      return;
    }
    event.preventDefault();
    const input = event.currentTarget;
    const start = input.selectionStart ?? draft.length;
    const end = input.selectionEnd ?? draft.length;
    add(parts, draft.slice(0, start) + draft.slice(end));
  }

  return (
    <div className="flex flex-col gap-2">
      <Field
        id={FIELD_IDS.synonyms}
        label={label}
        hint={refusal ?? "Press Enter or a semicolon to add a name."}
        error={error}
      >
        {(controlProps) => (
          <input
            {...controlProps}
            ref={box}
            className={INPUT_CLASSES}
            type="text"
            value={draft}
            onChange={(event) => type(event.target.value)}
            onKeyDown={commitOnEnter((value) => add([String(value)], "", draft), draft)}
            onPaste={paste}
          />
        )}
      </Field>
      <RemovableList
        label={`${label} added`}
        items={names.map((row) => ({
          id: row.id,
          label: row.term,
          removeLabel: `Remove ${row.term}`,
        }))}
        onRemove={(id) => {
          const removed = names.find((row) => row.id === id);
          setRefusal(null);
          onChange({ names: names.filter((row) => row.id !== id), nameDraft: draft });
          if (removed !== undefined) {
            announce(
              `Removed ${removed.term}. ${names.length - 1} left in the ${label} list.`,
            );
          }
        }}
        focusWhenEmpty={() => box.current?.focus()}
      />
      <LiveRegion message={message} politeness={politeness} />
    </div>
  );
}
