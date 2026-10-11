import { useMemo } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { useStore } from "../store";

/** Render markdown, then sanitize: report text may contain model- or user-written content. */
function safeMarkdown(md: string): string {
  const html = marked.parse(md, { async: false, gfm: true }) as string;
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, FORBID_TAGS: ["img", "style", "form", "input"] });
}

export default function ReportPage() {
  const { run } = useStore();
  const report = run?.context?.report;
  const html = useMemo(() => (report ? safeMarkdown(report.markdown) : ""), [report]);
  if (!run) return <p className="muted">Run the multi-agent analysis to generate the engineering report.</p>;
  if (!report) return <p className="muted">The Engineering Report Agent has not finished yet (status: {run.status}).</p>;
  const download = () => {
    const blob = new Blob([report.markdown], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `gridmind-report-${run._id}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <div style={{ maxWidth: 1100 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <span className="muted small">Generated {new Date(report.generated_at).toLocaleString()} · executive summary by {report.llm_used ? "Claude" : "template"}</span>
        <button onClick={download}>Download .md</button>
      </div>
      <div className="banner info" style={{ marginTop: 10 }}><b>Executive summary.</b> <span style={{ whiteSpace: "pre-wrap" }}>{report.executive_summary}</span></div>
      <article className="panel md" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
