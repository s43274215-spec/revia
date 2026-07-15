import { KeyboardEvent, useState } from "react";

export type MenuState = { x: number; y: number; pointId: string };

export function ContextMenu({ menu, onSingleEdit, onGlobalEdit, onDelete }: { menu: MenuState; onSingleEdit: () => void; onGlobalEdit: () => void; onDelete: () => void }) {
  const [editOpen, setEditOpen] = useState(false);
  const openWithKeyboard = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowRight" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setEditOpen(true);
      requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(".context-submenu-panel button")?.focus());
    }
  };

  return (
    <div className="context-menu" style={{ left: menu.x, top: menu.y }} role="menu" data-testid="context-menu" onClick={(event) => event.stopPropagation()}>
      <div className={`context-submenu ${editOpen ? "is-open" : ""}`} onMouseEnter={() => setEditOpen(true)} onPointerEnter={() => setEditOpen(true)} onMouseMove={() => { if (!editOpen) setEditOpen(true); }}>
        <button role="menuitem" aria-haspopup="menu" aria-expanded={editOpen} onClick={() => setEditOpen(true)} onFocus={() => setEditOpen(true)} onKeyDown={openWithKeyboard}>编辑 <span>›</span></button>
        <div className="context-submenu-panel" role="menu" style={editOpen ? { display: "block" } : undefined}>
          <button role="menuitem" onClick={onSingleEdit}>单独编辑</button>
          <button role="menuitem" onClick={onGlobalEdit}>整体编辑</button>
        </div>
      </div>
      <div className="context-divider" />
      <button className="danger" role="menuitem" onClick={onDelete}>删除要点</button>
    </div>
  );
}
