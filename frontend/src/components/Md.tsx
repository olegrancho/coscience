import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/** Project-wide markdown renderer. Always enables GitHub-flavoured markdown so
 *  tables, strikethrough, autolinks and task lists render instead of leaking
 *  through as raw `| … |` text. */
export default function Md({ children, components }: { children: string; components?: Components }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {children}
    </ReactMarkdown>
  );
}
