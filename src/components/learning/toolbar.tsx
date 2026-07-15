import { Icon } from "./icons";

type ToolbarProps = { query: string; onQueryChange: (value: string) => void; onExport: () => void; onUndo: () => void; onRedo: () => void; canUndo: boolean; canRedo: boolean };

export function Toolbar({ query, onQueryChange, onExport, onUndo, onRedo, canUndo, canRedo }: ToolbarProps) {
  return (
    <header className="toolbar">
      <div className="history-actions">
        <button aria-label="撤销" title="撤销" onClick={onUndo} disabled={!canUndo}><Icon name="undo" /></button>
        <button aria-label="重做" title="重做" onClick={onRedo} disabled={!canRedo}><Icon name="redo" /></button>
      </div>
      <div className="toolbar-search"><Icon name="search" size={17} /><input value={query} onChange={(e) => onQueryChange(e.target.value)} placeholder="搜索当前项目" aria-label="搜索当前项目" /></div>
      <button className="export-button" onClick={onExport}><Icon name="export" size={17} />导出</button>
    </header>
  );
}
