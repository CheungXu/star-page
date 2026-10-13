import { readFileSync } from "node:fs";
import path from "node:path";
import Link from "next/link";
import type { ReactNode } from "react";
import { LEGAL_DOCS, type LegalDocKey } from "./docs";

type Block =
  | { kind: "heading"; text: string }
  | { kind: "paragraph"; lines: string[] }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "table"; header: string[]; rows: string[][] };

/** 只支持协议正文用到的 Markdown 子集：二级标题、段落、有序/无序列表、表格、加粗、行内代码 */
function parseBlocks(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = source.split("\n");
  let i = 0;
  const splitRow = (line: string) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (line.startsWith("## ")) {
      blocks.push({ kind: "heading", text: line.slice(3).trim() });
      i += 1;
      continue;
    }
    if (line.startsWith("|")) {
      const header = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].startsWith("|")) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      blocks.push({ kind: "table", header, rows });
      continue;
    }
    const listMatch = /^(-|\d+\.) /.exec(line);
    if (listMatch) {
      const ordered = listMatch[1] !== "-";
      const pattern = ordered ? /^\d+\. / : /^- /;
      const items: string[] = [];
      while (i < lines.length && pattern.test(lines[i])) {
        items.push(lines[i].replace(pattern, ""));
        i += 1;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }
    const paragraph: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(## |\||- |\d+\. )/.test(lines[i])) {
      paragraph.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ kind: "paragraph", lines: paragraph });
  }
  return blocks;
}

function renderInline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    return part;
  });
}

function renderBlock(block: Block, index: number) {
  switch (block.kind) {
    case "heading":
      return <h2 key={index}>{block.text}</h2>;
    case "paragraph":
      return (
        <p key={index}>
          {block.lines.map((line, lineIndex) => (
            <span key={lineIndex}>
              {lineIndex > 0 && <br />}
              {renderInline(line)}
            </span>
          ))}
        </p>
      );
    case "list": {
      const items = block.items.map((item, itemIndex) => <li key={itemIndex}>{renderInline(item)}</li>);
      return block.ordered ? <ol key={index}>{items}</ol> : <ul key={index}>{items}</ul>;
    }
    case "table":
      return (
        <div key={index} className="legal-table-wrap">
          <table>
            <thead>
              <tr>{block.header.map((cell, cellIndex) => <th key={cellIndex}>{renderInline(cell)}</th>)}</tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{renderInline(cell)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

/** 协议页：正文来自 content/legal/*.md，构建时静态渲染 */
export function LegalDocument({ docKey }: { docKey: LegalDocKey }) {
  const doc = LEGAL_DOCS[docKey];
  const source = readFileSync(path.join(process.cwd(), "content", "legal", `${docKey}.md`), "utf8");

  return (
    <main className="legal-page">
      <header className="legal-header">
        <Link className="legal-back" href="/">← 返回首页</Link>
        <nav className="legal-nav" aria-label="协议目录">
          {Object.entries(LEGAL_DOCS).map(([key, item]) => (
            <Link key={key} href={item.href} className={key === docKey ? "is-active" : undefined}>
              {item.shortTitle}
            </Link>
          ))}
        </nav>
      </header>
      <article className="legal-article">
        <h1>{doc.title}</h1>
        {parseBlocks(source).map(renderBlock)}
      </article>
    </main>
  );
}
