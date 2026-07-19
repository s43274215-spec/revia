import { SearchResult } from "./reader-search";

export function SearchResults({
  query,
  results,
  activeIndex,
  onSelect,
  onPrevious,
  onNext,
  onClear,
}: {
  query: string;
  results: SearchResult[];
  activeIndex: number;
  onSelect: (index: number) => void;
  onPrevious: () => void;
  onNext: () => void;
  onClear: () => void;
}) {
  if (!query.trim()) return null;
  return (
    <section className="search-results-panel" aria-label="搜索结果">
      <header>
        <div><strong>{results.length} 个结果</strong><span>{results.length ? `${activeIndex + 1} / ${results.length}` : "未找到匹配内容"}</span></div>
        <div className="search-result-actions">
          <button type="button" disabled={!results.length} onClick={onPrevious} aria-label="上一个结果">↑</button>
          <button type="button" disabled={!results.length} onClick={onNext} aria-label="下一个结果">↓</button>
          <button type="button" onClick={onClear}>清空</button>
        </div>
      </header>
      {results.length ? (
        <ol>
          {results.map((result, index) => (
            <li key={result.id}>
              <button className={activeIndex === index ? "is-active" : ""} type="button" onClick={() => onSelect(index)}>
                <span>{result.kind}{result.chapter ? ` · ${result.chapter}` : ""}</span>
                <strong>{result.knowledgePoint || result.chapter}</strong>
                <p>{result.context}</p>
              </button>
            </li>
          ))}
        </ol>
      ) : <p className="search-empty">换一个关键词试试；资料章节、知识点标题和当前版本正文都会参与搜索。</p>}
    </section>
  );
}
