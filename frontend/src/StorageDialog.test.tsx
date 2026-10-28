import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import StorageDialog, { formatBytes } from "./components/StorageDialog";

it("shows backup and cleanup actions without exposing secrets", () => {
  const html = renderToStaticMarkup(<StorageDialog onClose={() => {}} />);
  expect(html).toContain("数据与存储");
  expect(html).toContain('href="/backup"');
  expect(html).toContain("不包含密钥、evidence");
  expect(html).toContain("restore_backup.py");
  expect(html).toContain("清理临时文件并压缩");
});

it("formats storage sizes", () => {
  expect(formatBytes(512)).toBe("512 B");
  expect(formatBytes(2048)).toBe("2 KB");
  expect(formatBytes(5 * 1024 ** 2)).toBe("5.0 MB");
  expect(formatBytes(2 * 1024 ** 3)).toBe("2.00 GB");
});
