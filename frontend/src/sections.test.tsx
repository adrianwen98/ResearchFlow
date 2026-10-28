import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import TaskContinuation from "./components/TaskContinuation";
import { parentTask, relatedGoal } from "./related";
it("persists parent association in the real task input without claiming shared checkpoint or files", () => {
  const input = relatedGoal(
    { task_id: "old_task", task: "Original goal", answer: "Saved answer" },
    "补充第二个地区的数据",
  );
  expect(parentTask(input)).toBe("old_task");
  expect(input).toContain("Original goal");
  expect(input).toContain("Saved answer");
  expect(input).toContain("独立后续任务，不继续原Checkpoint，不自动读取原文件");
  expect(input.split("\n")[0]).toBe("后续任务：补充第二个地区的数据");
  expect(parentTask("Unrelated task")).toBeUndefined();
  expect(parentTask("后续任务：x\n[关联任务: ../bad]\n")).toBeUndefined();
});
it("bounds related context and rejects accidental empty submission", () => {
  const data = {
    task_id: "old",
    task: "x".repeat(6000),
    answer: "a".repeat(5000),
  };
  expect(() => relatedGoal(data, "x")).toThrow();
  expect(() => relatedGoal(data, "x".repeat(3001))).toThrow();
  expect(relatedGoal(data, "x".repeat(3000)).length).toBeLessThanOrEqual(6000);
});
it("offers a clearly separate follow-up task rather than fake conversation", () => {
  const html = renderToStaticMarkup(
    <TaskContinuation busy={false} onSubmit={() => {}} onEdit={() => {}} />,
  );
  expect(html).toContain("补充要求，创建后续任务");
  expect(html).not.toContain("继续对话");
});
