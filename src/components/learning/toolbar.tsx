import { Icon } from "./icons";

type ToolbarProps = { query: string; onQueryChange: (value: string) => void; onExport: () => void; onRegenerate: () => void; regenerating: boolean; regenerationDisabled?: boolean; onUndo: () => void; onRedo: () => void; canUndo: boolean; canRedo: boolean };

export function Toolbar({ query, onQueryChange, onExport, onRegenerate, regenerating, regenerationDisabled = false, onUndo, onRedo, canUndo, canRedo }: ToolbarProps) {
  return (
    <header className="toolbar">
      <div className="history-actions">
        <button aria-label="撤销" title="撤销" onClick={onUndo} disabled={!canUndo}><Icon name="undo" /></button>
        <button aria-label="重做" title="重做" onClick={onRedo} disabled={!canRedo}><Icon name="redo" /></button>
      </div>
      <div className="toolbar-search"><Icon name="search" size={17} /><input value={query} onChange={(e) => onQueryChange(e.target.value)} placeholder="搜索当前项目" aria-label="搜索当前项目" /></div>
      <button className="regenerate-button" title={regenerationDisabled ? "演示模式不会发起重新生成" : undefined} onClick={onRegenerate} disabled={regenerating || regenerationDisabled}>{regenerating ? "正在重新生成" : regenerationDisabled ? "演示模式只读" : "重新生成学习材料"}</button>
      <button className="export-button" onClick={onExport}><Icon name="export" size={17} />导出</button>
    </header>
  );
}
