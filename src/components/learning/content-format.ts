export type ContentBlock = {
  kind: "paragraph" | "ordered" | "unordered";
  items: string[];
};

const orderedLine = /^\s*(?:\d+[.、)）]|[（(][一二三四五六七八九十\d]+[）)])\s*(.+)$/;
const unorderedLine = /^\s*[-•·]\s*(.+)$/;

export function classifyContentBlocks(content: string[]): ContentBlock[] {
  return content.flatMap((block) => {
    const lines = block.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const groups: ContentBlock[] = [];
    for (const line of lines.length > 0 ? lines : [block]) {
      const ordered = line.match(orderedLine);
      const unordered = line.match(unorderedLine);
      const kind: ContentBlock["kind"] = ordered ? "ordered" : unordered ? "unordered" : "paragraph";
      const item = ordered?.[1] ?? unordered?.[1] ?? line;
      const current = groups.at(-1);
      if (current?.kind === kind) {
        current.items.push(item);
      } else {
        groups.push({ kind, items: [item] });
      }
    }
    return groups;
  });
}
