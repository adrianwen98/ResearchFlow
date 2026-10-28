import { it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "react-markdown";
import { safeLink } from "./model";
it("renders hostile Markdown as safe report content", () => {
  const html = renderToStaticMarkup(
    <Markdown
      skipHtml
      components={{
        a: ({ href, children }) => (
          <a href={href ? safeLink(href) : undefined}>{children}</a>
        ),
        img: ({ alt }) => <span>{alt}</span>,
      }}
    >{`# Report\n<script>alert(1)</script>\n\n[attack](javascript:alert)\n\n![pixel](https://attacker.example/pixel)\n\nActual value: -0.487`}</Markdown>,
  );
  expect(html).not.toContain("<script>");
  expect(html).not.toContain('href="javascript:');
  expect(html).not.toContain("<img");
  expect(html).toContain("-0.487");
});
