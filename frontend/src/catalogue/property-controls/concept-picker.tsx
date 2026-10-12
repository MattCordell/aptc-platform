import { Combobox } from "@base-ui/react/combobox";
import { useRef, useState } from "react";

import { refusalDetail } from "../../api/conflicts.ts";
import {
  usePropertyValueOptions,
  usePropertyValueResolveQueries,
} from "../../api/queries.ts";
import { Button } from "../../components/button.tsx";
import { Field } from "../../components/field.tsx";
import { INPUT_CLASSES } from "../../components/input-classes.ts";
import { useDebouncedValue } from "../use-debounced-value.ts";
import { findCoded, isCodedValue, toCodedValue } from "../coded-value.ts";
import type { CodedValue } from "../coded-value.ts";
import type { ControlProps } from "./types.ts";
import { codeLabel, selectedLabel } from "./value-labels.ts";

/**
 * `ControlKind.CONCEPT_PICKER` - the `code` datatype. One type-ahead box over
 * the property's offerable values (FR-23, FR-52).
 *
 * Whether the list is short or long comes from the server's own page, never
 * from the property's datatype or binding target (FR-77, ADR-0013): when
 * `total` fits in the unfiltered page the browser holds the whole list and
 * narrows it as the user types; otherwise the typed text goes to the server,
 * debounced, and the popup says when more matched than it shows.
 *
 * Without `onCommit` the box holds one value and shows the chosen code, with a
 * Clear button. With it, a pick is handed to `onCommit` and the box empties for
 * the next search; the caller lists what was picked.
 */

const SEARCH_DEBOUNCE_MS = 400;

const ITEM_CLASSES =
  "flex min-h-8 cursor-pointer select-none items-center gap-2 px-2 py-1 text-sm text-[var(--color-text)] data-[highlighted]:bg-[var(--color-accent)] data-[highlighted]:text-[var(--color-accent-contrast)]";

interface Option {
  value: CodedValue;
  /** How the option reads in the list. */
  label: string;
  /** How the value reads once chosen. */
  name: string;
}

const itemLabel = (option: Option): string => option.label;

export function ConceptPickerControl({
  id,
  label,
  hint,
  error,
  value,
  onChange,
  onCommit,
  propertyKey,
}: ControlProps) {
  const entry = onCommit !== undefined;
  const chosen = !entry && isCodedValue(value) ? value : null;
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState(false);
  const highlighted = useRef(false);

  const searching = entry || editing;
  const typedText = searching ? text.trim() : "";
  const debounced = useDebouncedValue(typedText, SEARCH_DEBOUNCE_MS);

  const everything = usePropertyValueOptions(propertyKey, "");
  const wholeList =
    everything.data !== undefined &&
    everything.data.total <= everything.data.items.length;
  const serverQuery = wholeList ? "" : debounced;
  const narrowed = usePropertyValueOptions(propertyKey, serverQuery);
  const resolved = usePropertyValueResolveQueries([
    { key: propertyKey, codes: chosen === null ? [] : [chosen.code] },
  ])[0];

  const settled = wholeList || typedText === "" || debounced === typedText;
  const page = typedText === "" || wholeList ? everything : narrowed;
  const needle = typedText.toLowerCase();
  const options: Option[] =
    settled && page.data !== undefined
      ? page.data.items
          .map((item) => ({
            value: toCodedValue(item),
            label: codeLabel(item.code, item.display),
            name: selectedLabel(item.code, item.display),
          }))
          .filter((option) => !wholeList || option.label.toLowerCase().includes(needle))
      : [];

  const failure = everything.isError
    ? everything.error
    : !wholeList && settled && typedText !== "" && narrowed.isError
      ? narrowed.error
      : null;
  const loadFailed = failure !== null;
  const loading =
    everything.isPending ||
    (typedText !== "" && !wholeList && (!settled || narrowed.isFetching));
  const total = page.data?.total ?? null;
  const truncated = !wholeList && total !== null && total > options.length;

  const statusText = loadFailed
    ? null
    : loading
      ? "Searching…"
      : options.length === 0 && typedText !== ""
        ? "No value matches what you typed."
        : truncated
          ? `Showing ${options.length} of ${total}. Type more to narrow the list.`
          : null;
  const loadError = loadFailed
    ? `${
        refusalDetail(failure) ?? "The list of values could not be loaded."
      } Reload the page to try again.`
    : undefined;

  const chosenLabel =
    chosen === null
      ? ""
      : selectedLabel(
          chosen.code,
          findCoded(resolved.data?.items, chosen)?.display ??
            findCoded(everything.data?.items, chosen)?.display ??
            chosen.display,
        );
  const inputValue = searching ? text : chosenLabel;

  function pick(option: Option) {
    if (onCommit !== undefined) {
      onCommit(option.value, option.name);
    } else {
      onChange(option.value);
    }
    setText("");
    setEditing(false);
    setOpen(false);
  }

  return (
    <Field
      id={id}
      label={label}
      hint={
        hint ??
        (entry
          ? "Type to search, then choose a value to add it to the list."
          : "Type to search the list.")
      }
      error={error ?? loadError}
    >
      {(controlProps) => (
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <Combobox.Root<Option, false>
              items={options}
              filter={null}
              onItemHighlighted={(item) => {
                highlighted.current = item !== undefined;
              }}
              value={null}
              inputValue={inputValue}
              open={open && (options.length > 0 || statusText !== null)}
              onOpenChange={setOpen}
              itemToStringLabel={itemLabel}
              onInputValueChange={(next, details) => {
                if (details.reason === "input-change") {
                  setText(next);
                  setEditing(true);
                  setOpen(true);
                }
              }}
              onValueChange={(item) => {
                if (item !== null) {
                  pick(item);
                }
              }}
            >
              <Combobox.Input
                {...controlProps}
                // Base UI hides the visible label from assistive technology while
                // the popup is open, which would leave the input unnamed.
                aria-label={label}
                className={INPUT_CLASSES}
                autoComplete="off"
                onFocus={(event) => {
                  if (!entry) {
                    event.currentTarget.select();
                  }
                }}
                onBlur={() => {
                  if (!entry) {
                    setEditing(false);
                    setText("");
                  }
                }}
                onKeyDown={(event) => {
                  // Enter in a form's text box would submit the whole form. Here it
                  // chooses the highlighted result, or the first when none is.
                  if (event.key !== "Enter") {
                    return;
                  }
                  event.preventDefault();
                  const first = options[0];
                  if (open && typedText !== "" && !highlighted.current && first) {
                    pick(first);
                  }
                }}
              />
              <Combobox.Portal>
                <Combobox.Positioner sideOffset={4} className="z-50 outline-none">
                  <Combobox.Popup className="max-h-72 w-[var(--anchor-width)] max-w-[var(--available-width)] overflow-y-auto rounded-[var(--radius-control)] border border-[var(--color-border)] bg-[var(--color-surface)] py-1">
                    {/* Inside the popup, because Base UI marks everything outside it
                        inert while it is open, and an inert live region is not read. */}
                    <Combobox.Status className="text-sm text-[var(--color-text-muted)]">
                      {statusText === null ? null : (
                        <p className="m-0 px-2 py-1">{statusText}</p>
                      )}
                    </Combobox.Status>
                    <Combobox.List>
                      {(option: Option) => (
                        <Combobox.Item
                          key={`${option.value.system}|${option.value.code}`}
                          value={option}
                          className={ITEM_CLASSES}
                        >
                          {option.label}
                        </Combobox.Item>
                      )}
                    </Combobox.List>
                  </Combobox.Popup>
                </Combobox.Positioner>
              </Combobox.Portal>
            </Combobox.Root>
          </div>
          {chosen === null ? null : (
            <Button
              type="button"
              variant="secondary"
              aria-label={`Clear ${label}`}
              onClick={() => {
                onChange(null);
                setText("");
                setEditing(false);
              }}
            >
              Clear
            </Button>
          )}
        </div>
      )}
    </Field>
  );
}
