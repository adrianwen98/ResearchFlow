import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import DataTable, {
  isNumericValue,
  numericColumnFlags,
} from "./components/DataTable";

it("recognises numeric strings from workbook JSON but not narrative values", () => {
  for (const value of [12, 0, "1269.4", "1,234.5", "-3", "+8", "12%", " 7 "])
    expect(isNumericValue(value)).toBe(true);
  for (const value of [null, "", "未核实", "官方未公布", "1978年", "约 2", "N/A"])
    expect(isNumericValue(value)).toBe(false);
});

it("marks a column numeric only when every disclosed value is numeric", () => {
  expect(
    numericColumnFlags(2, [
      ["1978", "884"],
      ["1980", "1269.4"],
      ["1985", "未核实"],
    ]),
  ).toEqual([true, true]);
  expect(
    numericColumnFlags(2, [
      ["A", "1"],
      ["B", "约 2"],
    ]),
  ).toEqual([false, false]);
  expect(numericColumnFlags(1, [["未核实"]])).toEqual([false]);
});

it("right-aligns numeric columns and keeps missing values readable", () => {
  const html = renderToStaticMarkup(
    <DataTable
      columns={["对象", "年份", "氮肥（万吨）"]}
      rows={[
        ["1978", "1978", "884"],
        ["1980", "1980", "1269.4"],
        ["1985", "1985", "未核实"],
        ["1990", "1990", null],
      ]}
    />,
  );
  expect((html.match(/<th[^>]*numeric-cell/g) || []).length).toBe(3);
  expect(html).toContain('<td class="numeric-cell" title="1269.4">1269.4</td>');
  expect(html).toContain('title="未核实"');
  expect(html).toContain('class="missing-cell"');
  expect(html).toContain("空值");
});

it("keeps text columns left aligned and exposes full cell text as a tooltip", () => {
  const html = renderToStaticMarkup(
    <DataTable
      columns={["名称", "数量"]}
      rows={[
        ["苹果", "12"],
        ["这是一段很长的说明文字", "未核实"],
      ]}
    />,
  );
  expect((html.match(/<th[^>]*numeric-cell/g) || []).length).toBe(1);
  expect(html).toContain('title="这是一段很长的说明文字"');
  expect(html).toContain("表格数据，可横向滚动");
});
