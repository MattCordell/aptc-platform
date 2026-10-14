import { describe, expect, it } from "vitest";

import {
  defaultStateFilter,
  isSubmissionStateFilter,
  stateFilterOptions,
  statesForRequest,
  submissionStateLabel,
  submissionStateTone,
  SUBMISSION_STATES,
} from "./state-options.ts";

describe("submission states (FR-28)", () => {
  it("lists all five states", () => {
    expect(SUBMISSION_STATES).toEqual([
      "Submitted",
      "Triage",
      "Published in release",
      "Rejected",
      "Withdrawn",
    ]);
  });

  it("labels each state with its own name", () => {
    for (const state of SUBMISSION_STATES) {
      expect(submissionStateLabel(state)).toBe(state);
    }
  });

  it("degrades an unknown state to its own text and a neutral tone", () => {
    expect(submissionStateLabel("Escalated")).toBe("Escalated");
    expect(submissionStateTone("Escalated")).toBe("neutral");
  });
});

describe("statesForRequest", () => {
  it("sends nothing for an absent filter, so the server's own default applies", () => {
    expect(statesForRequest(undefined)).toBeUndefined();
  });

  it("sends the open states for Open", () => {
    expect(statesForRequest("open")).toEqual(["Submitted", "Triage"]);
  });

  it("sends every state for All states", () => {
    expect(statesForRequest("all")).toEqual(SUBMISSION_STATES);
  });

  it("sends one state alone", () => {
    expect(statesForRequest("Withdrawn")).toEqual(["Withdrawn"]);
  });
});

describe("state filter", () => {
  it("defaults to open in the All view and to every state in My submissions", () => {
    expect(defaultStateFilter(false)).toBe("open");
    expect(defaultStateFilter(true)).toBe("all");
  });

  it("offers the two groups first, then each state", () => {
    expect(stateFilterOptions().map((option) => option.label)).toEqual([
      "Open",
      "All states",
      ...SUBMISSION_STATES,
    ]);
  });

  it("accepts every option it offers and nothing else", () => {
    for (const option of stateFilterOptions()) {
      expect(isSubmissionStateFilter(option.value)).toBe(true);
    }
    expect(isSubmissionStateFilter("withdrawn")).toBe(false);
  });
});
