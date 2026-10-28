import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import TaskHistoryItem from "./components/TaskHistoryItem";
import {
  CleanupOptions,
  retainedOptions,
} from "./components/TaskHistoryDialog";
import type { Task, DeletionPreview } from "./types";

const task: Task = {
  task_id: "history-test",
  task: "Original immutable goal",
  title: "A long title <script>inert</script> " + "研究任务".repeat(30),
  status: "needs_review",
  status_label: "结果待确认",
  created_at: "",
  metrics: {},
  elapsed_seconds: null,
  read_only: false,
  execution_label: "",
};
const preview: DeletionPreview = {
  task_id: task.task_id,
  groups: {
    records: { files: 7, bytes: 100 },
    files: { files: 3, bytes: 200 },
  },
  checkpoint: { checkpoints: 4, writes: 9 },
  active: false,
  locked: false,
  can_stop: false,
  can_delete: true,
  cleanup_pending: false,
};

it("preserves a full title tooltip, real status and separate accessible menu trigger", () => {
  const html = renderToStaticMarkup(
    <TaskHistoryItem
      task={task}
      selected
      busy={false}
      onChoose={() => {}}
      onAction={() => {}}
    />,
  );
  expect(html).toContain('aria-current="page"');
  expect(html).toContain(
    'title="A long title &lt;script&gt;inert&lt;/script&gt;',
  );
  expect(html).toContain("结果待确认");
  expect(html).toContain('class="task-status warning"');
  expect(html).toContain('aria-haspopup="menu"');
  expect((html.match(/<button/g) || []).length).toBe(2);
  expect(html).not.toContain("<script>");
});
it("does not preselect destructive cleanup options", () => {
  const html = renderToStaticMarkup(
    <CleanupOptions
      value={retainedOptions}
      onChange={() => {}}
      disabled={false}
      preview={preview}
    />,
  );
  expect((html.match(/type="checkbox"/g) || []).length).toBe(3);
  expect(html).not.toContain('checked=""');
  expect(html).toContain("7 个文件");
  expect(html).toContain("4 个保存点");
  expect(html).toContain("不影响其他任务和通用记忆");
});
it("supports selecting only checkpoints while all controls can be disabled by the server gate", () => {
  const html = renderToStaticMarkup(
    <CleanupOptions
      value={{ ...retainedOptions, delete_checkpoint: true }}
      onChange={() => {}}
      disabled
      preview={preview}
    />,
  );
  expect((html.match(/checked=""/g) || []).length).toBe(1);
  expect(html).toContain('<fieldset class="cleanup-options" disabled=""');
});
it("retains the recorded-demo distinction in history", () => {
  const html = renderToStaticMarkup(
    <TaskHistoryItem
      task={{ ...task, read_only: true, archived: true }}
      selected={false}
      busy={false}
      onChoose={() => {}}
      onAction={() => {}}
    />,
  );
  expect(html).toContain("演示任务");
  expect(html).toContain('class="task-status neutral"');
  expect(html).not.toContain('aria-current="page"');
});
