export type CellValue = string | number | boolean | null;

const NUMERIC = /^[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$/;
const NOT_DISCLOSED = new Set(["未核实", "官方未公布"]);

/** Workbook values arrive from JSON, so "1269.4" must count as numeric too. */
export function isNumericValue(value: CellValue | undefined): boolean {
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "string") return false;
  return NUMERIC.test(value.trim());
}

/** A column is numeric only when every disclosed value in it is numeric. */
export function numericColumnFlags(
  columnCount: number,
  rows: CellValue[][],
): boolean[] {
  return Array.from({ length: columnCount }, (_, index) => {
    let seen = false;
    for (const row of rows) {
      const value = row[index];
      if (
        value === null ||
        value === undefined ||
        value === "" ||
        (typeof value === "string" && NOT_DISCLOSED.has(value))
      ) {
        continue;
      }
      if (!isNumericValue(value)) return false;
      seen = true;
    }
    return seen;
  });
}

export default function DataTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: CellValue[][];
}) {
  const numeric = numericColumnFlags(columns.length, rows);
  return (
    <div
      className="data-table"
      tabIndex={0}
      role="region"
      aria-label="表格数据，可横向滚动"
    >
      <table>
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th
                scope="col"
                key={index}
                className={numeric[index] ? "numeric-cell" : ""}
                title={column}
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((value, cellIndex) => {
                const missing =
                  typeof value === "string" && NOT_DISCLOSED.has(value);
                const empty = value === null || value === undefined;
                const display = empty ? "空值" : String(value);
                return (
                  <td
                    key={cellIndex}
                    className={isNumericValue(value) ? "numeric-cell" : ""}
                    title={display}
                  >
                    {missing ? (
                      <span className="missing-cell">{value}</span>
                    ) : empty ? (
                      <span className="null">空值</span>
                    ) : (
                      String(value)
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
