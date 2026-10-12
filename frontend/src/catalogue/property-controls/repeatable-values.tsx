import { useRef, useState } from "react";

import type { components } from "../../api/schema.ts";
import { Field } from "../../components/field.tsx";
import { INPUT_CLASSES } from "../../components/input-classes.ts";
import type { FormError } from "../../components/error-summary.tsx";
import { LiveRegion } from "../../components/live-region.tsx";
import { RemovableList } from "../../components/removable-list.tsx";
import { useAnnounce } from "../../components/use-announce.ts";
import { isCodedValue } from "./coded-value.ts";
import { CONTROLS } from "./controls.ts";
import {
  groupFieldId,
  isEmptySlotValue,
  newSlotId,
  sameSlotValue,
  slotFieldId,
} from "./types.ts";
import type { ControlKind, PropertyValueSlot } from "./types.ts";
import { VALUE_LABELS, codeLabel } from "./value-labels.ts";

type PropertyCardinality = components["schemas"]["PropertyCardinality"];

const MULTI: Record<PropertyCardinality, boolean> = {
  "0..1": false,
  "1..1": false,
  "0..*": true,
  "1..*": true,
};

interface RepeatableValuesProps {
  propertyKey: string;
  label: string;
  cardinality: PropertyCardinality;
  control: ControlKind;
  params: Record<string, unknown>;
  slots: PropertyValueSlot[];
  onChange: (slots: PropertyValueSlot[]) => void;
  errors: FormError[];
  /**
   * Leaves out the hint a control shows by default on a property with one value, for a form
   * that has to fit one screen. A property with several values keeps its short cue for
   * adding, because the user needs it before typing.
   */
  compact?: boolean;
}

/**
 * Renders one property's whole value set - the cardinality wrapper, kept
 * outside every control module so no control needs to know whether it is
 * being asked for one value or several (issue #151).
 *
 * A property with one value shows its control, holding that value. A property
 * with several shows the same control as an entry box and lists what was added
 * below it. `slots` is the whole set either way; `onChange` reports it after
 * every edit, and `isEmptySlotValue` in the caller's own submit handling drops
 * a slot nobody filled.
 *
 * A slot's ordinal is its position in `slots` - matching
 * `PropertyValue.ordinal`'s own contract - so a Remove does not leave a gap; it
 * re-numbers everything after it, same as the server does on a whole-set
 * replace.
 *
 * `params.allowJustification` is read here rather than inside a control:
 * today only the `code` handler ever sets it (`registry/datatypes/code.py`),
 * but the justification it gates is a slot-level editorial concept - why
 * *this recorded value* deviates from the bound list - not something tied
 * to how any one control renders a value. Centralising it here means a
 * future control that also needs it gets the same UI for free instead of
 * reimplementing it; the ADR-0013 AST guard cannot see this read because it
 * is not named `datatype` - deliberately not a proxy switch, just a param
 * the wrapper interprets like any other in `params`.
 */
export function RepeatableValues(props: RepeatableValuesProps) {
  return MULTI[props.cardinality] ? (
    <SeveralValues {...props} />
  ) : (
    <OneValue {...props} />
  );
}

function JustificationField({
  id,
  label,
  slot,
  onChange,
}: {
  id: string;
  label: string;
  slot: PropertyValueSlot;
  onChange: (justification: string) => void;
}) {
  return (
    <Field
      id={id}
      label={label}
      hint="Required when this value is not in the bound value set."
    >
      {(controlProps) => (
        <input
          {...controlProps}
          className={INPUT_CLASSES}
          type="text"
          value={slot.justification ?? ""}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Field>
  );
}

function OneValue({
  propertyKey,
  label,
  control,
  params,
  slots,
  onChange,
  errors,
  compact = false,
}: RepeatableValuesProps) {
  const Control = CONTROLS[control];
  // Stable for the component's lifetime, not regenerated each render - see
  // `PropertyValueSlot.id`'s own doc: this placeholder is only ever shown
  // while `slots` is empty, and the first keystroke into it carries this id
  // into the caller's own state via `onChange` below.
  const [placeholderId] = useState(newSlotId);
  const slot: PropertyValueSlot = slots[0] ?? {
    id: placeholderId,
    value: null,
    justification: null,
  };
  const fieldId = slotFieldId(propertyKey, 0);
  const groupError = errors.find(
    (error) => error.fieldId === groupFieldId(propertyKey),
  )?.message;

  return (
    <div id={groupFieldId(propertyKey)} tabIndex={-1} className="flex flex-col gap-3">
      <Control
        id={fieldId}
        label={label}
        hint={compact ? false : undefined}
        error={errors.find((error) => error.fieldId === fieldId)?.message}
        propertyKey={propertyKey}
        params={params}
        value={slot.value}
        onChange={(value) => onChange([{ ...slot, value }])}
      />
      {params.allowJustification === true && (
        <JustificationField
          id={`${fieldId}-justification`}
          label="Justification"
          slot={slot}
          onChange={(justification) => onChange([{ ...slot, justification }])}
        />
      )}
      {groupError && <p className="text-sm text-[var(--color-danger)]">{groupError}</p>}
    </div>
  );
}

function SeveralValues({
  propertyKey,
  label,
  control,
  params,
  slots,
  onChange,
  errors,
}: RepeatableValuesProps) {
  const Control = CONTROLS[control];
  const { message, politeness, announce } = useAnnounce();
  // A label the picker handed over at the moment of adding, shown until the
  // server's own answer for that code arrives.
  const [known, setKnown] = useState<ReadonlyMap<unknown, string>>(new Map());
  const container = useRef<HTMLDivElement>(null);

  const added = slots.filter((slot) => slot.draft !== true);
  const draft = slots.find((slot) => slot.draft === true);
  const boxId = slotFieldId(propertyKey, added.length);
  const resolved = VALUE_LABELS[control](
    propertyKey,
    added.map((slot) => slot.value),
  );
  const labelOf = (index: number): string => {
    const { value } = added[index];
    return (
      resolved[index] ??
      known.get(value) ??
      (isCodedValue(value) ? codeLabel(value.code, value.display) : String(value))
    );
  };
  const allowJustification = params.allowJustification === true;
  const errorFor = (fieldId: string) =>
    errors.find((error) => error.fieldId === fieldId)?.message;
  const groupError = errorFor(groupFieldId(propertyKey));

  function setJustification(index: number, justification: string) {
    onChange(
      slots.map((slot) => (slot === added[index] ? { ...slot, justification } : slot)),
    );
  }

  function setDraft(value: unknown) {
    onChange(
      isEmptySlotValue(value)
        ? added
        : [
            ...added,
            {
              id: draft?.id ?? newSlotId(),
              value,
              justification: null,
              draft: true,
            },
          ],
    );
  }

  function commit(value: unknown, name?: string) {
    const text = name ?? String(value);
    if (added.some((slot) => sameSlotValue(slot.value, value))) {
      announce(`${text} is already in the ${label} list.`);
      onChange(added);
      return;
    }
    if (name !== undefined) {
      setKnown((previous) => new Map(previous).set(value, name));
    }
    onChange([...added, { id: newSlotId(), value, justification: null }]);
    announce(`Added ${text}. ${added.length + 1} in the ${label} list.`);
  }

  return (
    <div
      id={groupFieldId(propertyKey)}
      ref={container}
      tabIndex={-1}
      className="flex flex-col gap-3"
    >
      <Control
        id={boxId}
        label={label}
        error={errorFor(boxId)}
        propertyKey={propertyKey}
        params={params}
        value={draft?.value ?? null}
        onChange={setDraft}
        onCommit={commit}
      />
      <RemovableList
        label={`${label} values`}
        items={added.map((slot, index) => {
          const fieldId = slotFieldId(propertyKey, index);
          const name = labelOf(index);
          return {
            id: slot.id,
            elementId: fieldId,
            label: name,
            removeLabel: `Remove ${name}`,
            error: errorFor(fieldId),
            children: allowJustification ? (
              <JustificationField
                id={`${fieldId}-justification`}
                label={`Justification for ${name}`}
                slot={slot}
                onChange={(justification) => setJustification(index, justification)}
              />
            ) : null,
          };
        })}
        onRemove={(id) => {
          const index = added.findIndex((slot) => slot.id === id);
          const name = labelOf(index);
          onChange(slots.filter((slot) => slot.id !== id));
          announce(`Removed ${name}. ${added.length - 1} left in the ${label} list.`);
        }}
        focusWhenEmpty={() =>
          container.current?.querySelector<HTMLElement>("input, textarea")?.focus()
        }
      />
      {groupError && <p className="text-sm text-[var(--color-danger)]">{groupError}</p>}
      <LiveRegion message={message} politeness={politeness} />
    </div>
  );
}
