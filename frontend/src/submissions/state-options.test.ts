import { describe, expect, it } from "vitest";

import {
  defaultStateFilter,
  isOpenSubmissionState,
  isSubmissionStateFilter,
  stateFilterOptions,
  statesForRequest,
  submissionStateLabel,
  submissionStateTone,
  SUBMISSION_STATES,
} from "./state-options.ts";

describe("submission states (FR-28)", () => {
  it("lists every state, in the order of the workflow", () => {
    expect(SUBMISSION_STATES).toEqual([
      "Submitted",
      "Triage",
      "Under review",
      "Awaiting terminology",
      "Ready for approval",
      "Approved",
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

  it("counts every state before release as open, Approved included, and the rest as closed", () => {
    const open = SUBMISSION_STATES.filter(isOpenSubmissionState);
    expect(open).toEqual([
      "Submitted",
      "Triage",
      "Under review",
      "Awaiting terminology",
      "Ready for approval",
      "Approved",
    ]);
  });

  it("gives each review state a badge tone", () => {
    expect(submissionStateTone("Under review")).toBe("draft");
    expect(submissionStateTone("Awaiting terminology")).toBe("draft");
    expect(submissionStateTone("Ready for approval")).toBe("draft");
    expect(submissionStateTone("Approved")).toBe("active");
  });

  it("does not count an unknown state as open", () => {
    expect(isOpenSubmissionState("Escalated")).toBe(false);
    expect(isOpenSubmissionState("toString")).toBe(false);
  });
});

describe("statesForRequest", () => {
  it("sends nothing for an absent filter, so the server's own default applies", () => {
    expect(statesForRequest(undefined)).toBeUndefined();
  });

  it("sends the open states for Open", () => {
    expect(statesForRequest("open")).toEqual([
      "Submitted",
      "Triage",
      "Under review",
      "Awaiting terminology",
      "Ready for approval",
      "Approved",
    ]);
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
