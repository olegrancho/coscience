import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ZoomableImg } from "./ui";

/** Images embedded in markdown are laid out at column width, which for a plot is
 *  too small to read. Click opens it full-size. Applied here so every rendered
 *  document — reports, chat messages, ideas, sprint goals — gets it. */
const imgRenderer = (resolveSrc?: (src: string) => string): Components => ({
  img: ({ node: _node, src, ...props }) => {
    // A document that ships its own figures references them by relative path
    // ("figures/fig1.png"), which the browser would resolve against the dashboard
    // route. The caller says where those files actually live.
    const absolute = typeof src === "string" && /^(https?:|data:|blob:|\/)/.test(src);
    const resolved = resolveSrc && typeof src === "string" && !absolute ? resolveSrc(src) : src;
    return <ZoomableImg {...props} src={resolved} style={{ maxWidth: "100%" }} />;
  },
});

/** Project-wide markdown renderer. Always enables GitHub-flavoured markdown so
 *  tables, strikethrough, autolinks and task lists render instead of leaking
 *  through as raw `| … |` text. `resolveSrc` maps a document-relative image path
 *  to a URL that serves it. */
export default function Md(
  { children, components, resolveSrc }:
  { children: string; components?: Components; resolveSrc?: (src: string) => string },
) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]}
                   components={{ ...imgRenderer(resolveSrc), ...components }}>
      {children}
    </ReactMarkdown>
  );
}
