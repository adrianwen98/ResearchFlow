import { describe, it, expect } from "vitest";
import {
  mergeEvents,
  stage,
  safeLink,
  stateInfo,
  toolCalls,
  callFailed,
} from "./model";
describe("real state rendering contract", () => {
  it("never conflates pending delivery with completed", () => {
    expect(stage("needs_review", [])).toBe(3);
    expect(stage("completed", [])).toBe(4);
    expect(stateInfo("needs_review").label).toBe("结果待确认");
    expect(stateInfo("awaiting_review").label).not.toBe(
      stateInfo("completed").label,
    );
  });
  it("merges reconnect/REST/SSE events once and in order", () => {
    const a = {
      id: 1,
      event: "tool_start",
      call_id: "x",
      detail: "browser_click",
    };
    const b = {
      id: 2,
      event: "tool_end",
      call_id: "x",
      output_preview: "actual",
    };
    expect(mergeEvents([a, b], [a, b])).toEqual([a, b]);
    expect(toolCalls([a, b])[0].end?.output_preview).toBe("actual");
  });
  it("shows a recovered tool as returned while preserving the prior error", () => {
    const call = toolCalls([
      { id: 1, event: "tool_start", call_id: "x", detail: "browser_snapshot" },
      { id: 2, event: "tool_error", call_id: "x", detail: "timeout" },
      { id: 3, event: "tool_retry", call_id: "x" },
      { id: 4, event: "tool_end", call_id: "x", output_preview: "Actual page" },
    ])[0];
    expect(callFailed(call)).toBe(false);
    expect(call.error?.detail).toBe("timeout");
    expect(call.retry).toBe(1);
  });
  it("blocks active-content and credential links", () => {
    expect(safeLink("javascript:alert(1)")).toBeUndefined();
    expect(safeLink("data:text/html,hello")).toBeUndefined();
    expect(safeLink("https://u:p@example.org")).toBeUndefined();
    expect(safeLink("https://python.org")).toBe("https://python.org");
  });
  it("distinguishes permission, budget, partial, approval, and failure", () => {
    const labels = [
      "permission_denied",
      "budget_exceeded",
      "partial",
      "awaiting_review",
      "error",
    ].map((x) => stateInfo(x).label);
    expect(new Set(labels).size).toBe(5);
  });
});
