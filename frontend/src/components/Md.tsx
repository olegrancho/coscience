import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ZoomableImg } from "./ui";

/** Images embedded in markdown are laid out at column width, which for a plot is
 *  too small to read. Click opens it full-size. Applied here so every rendered
 *  document — reports, chat messages, ideas, sprint goals — gets it. */
const base: Components = {
  img: ({ node: _node, ...props }) => <ZoomableImg {...props} style={{ maxWidth: "100%" }} />,
};

/** Project-wide markdown renderer. Always enables GitHub-flavoured markdown so
 *  tables, strikethrough, autolinks and task lists render instead of leaking
 *  through as raw `| … |` text. */
export default function Md({ children, components }: { children: string; components?: Components }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ ...base, ...components }}>
      {children}
    </ReactMarkdown>
  );
}
