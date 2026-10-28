import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeLink } from "../model";
export default function ReportMarkdown({ children }: { children: string }) {
  return (
    <Markdown
      remarkPlugins={[remarkGfm]}
      skipHtml
      components={{
        a: ({ href, children }) => (
          <a
            href={href ? safeLink(href) : undefined}
            target="_blank"
            rel="noopener noreferrer"
          >
            {children}
          </a>
        ),
        img: ({ alt }) => (
          <span className="muted">[图片：{alt ?? "未加载外部图片"}]</span>
        ),
      }}
    >
      {children}
    </Markdown>
  );
}
