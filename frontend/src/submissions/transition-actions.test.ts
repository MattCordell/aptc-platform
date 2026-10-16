import { describe, expect, it } from "vitest";

import type { SubmissionState } from "./state-options.ts";
import { isMadeOnTheTriagePanel, moveWording } from "./transition-actions.ts";

/**
 * The words for each move and the moves the Triage panel leaves to other
 * screens (FR-28). The second list is a small second source of truth about the
 * state graph, so it is pinned here move by move.
 */

describe("isMadeOnTheTriagePanel", () => {
  it.each<[string, SubmissionState]>([
    ["Triage", "Withdrawn"],
    ["Under review", "Awaiting terminology"],
    ["Awaiting terminology", "Ready for approval"],
    ["Ready for approval", "Approved"],
  ])("leaves %s to %s to another screen", (from, to) => {
    expect(isMadeOnTheTriagePanel(from, to)).toBe(false);
  });

  it.each<[string, SubmissionState]>([
    ["Submitted", "Triage"],
    ["Triage", "Under review"],
    ["Triage", "Rejected"],
    ["Under review", "Rejected"],
    ["Ready for approval", "Rejected"],
    ["Rejected", "Triage"],
    ["Under review", "Ready for approval"],
    ["Awaiting terminology", "Under review"],
  ])("keeps %s to %s on the panel", (from, to) => {
    expect(isMadeOnTheTriagePanel(from, to)).toBe(true);
  });

  it("skips Ready for approval only from Awaiting terminology", () => {
    expect(isMadeOnTheTriagePanel("Under review", "Ready for approval")).toBe(true);
    expect(isMadeOnTheTriagePanel("Awaiting terminology", "Ready for approval")).toBe(
      false,
    );
  });
});

describe("moveWording", () => {
  it("gives every move the panel makes its own label and effect", () => {
    const moves: [string, SubmissionState][] = [
      ["Submitted", "Triage"],
      ["Triage", "Under review"],
      ["Triage", "Rejected"],
      ["Rejected", "Triage"],
      ["Under review", "Ready for approval"],
      ["Awaiting terminology", "Under review"],
    ];
    const labels = moves.map(([from, to]) => moveWording(from, to, to).label);

    expect(new Set(labels).size).toBe(moves.length);
    for (const [from, to] of moves) {
      expect(moveWording(from, to, to).label).not.toMatch(/^Move to /);
    }
  });

  it.each<[string, SubmissionState]>([
    ["Triage", "Rejected"],
    ["Under review", "Rejected"],
    ["Ready for approval", "Rejected"],
  ])("words the rejection from %s as Reject", (from, to) => {
    const wording = moveWording(from, to, to);

    expect(wording.label).toBe("Reject");
    expect(wording.effect).toMatch(/reopen a rejected submission/);
  });

  it("labels a move it has no words for with its target, so no move is hidden", () => {
    expect(moveWording("Rejected", "Under review", "Under review")).toEqual({
      label: "Move to Under review",
      effect: "The submission moves to Under review.",
    });
  });
});
