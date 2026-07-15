import { useState } from "react";
import { KnowledgePoint, PointVersions, Version, VersionPoint, versionLabels } from "./data";
import { Icon } from "./icons";

export type DrawerState =
  | { mode: "keyword"; point: KnowledgePoint }
  | { mode: "single"; point: KnowledgePoint; version: Version }
  | { mode: "global"; point: KnowledgePoint; version: Version };

const toText = (items: string[]) => items.join("\n\n");
const fromText = (value: string) => value.split(/\n\s*\n/).map((item) => item.trim()).filter(Boolean);

export function OperationDrawer({ state, onClose, onSaveSingle, onSaveGlobal }: { state: DrawerState; onClose: () => void; onSaveSingle: (pointId: string, version: Version, value: VersionPoint) => void; onSaveGlobal: (pointId: string, versions: PointVersions) => void }) {
  const initialVersion = state.mode === "keyword" ? "recitation" : state.version;
  const initialValue = state.point.versions[initialVersion];
  const [singleTitle, setSingleTitle] = useState(initialValue.title);
  const [singleText, setSingleText] = useState(toText(initialValue.content));
  const [globalDraft, setGlobalDraft] = useState<Record<Version, { title: string; content: string }>>({
    original: { title: state.point.versions.original.title, content: toText(state.point.versions.original.content) },
    recitation: { title: state.point.versions.recitation.title, content: toText(state.point.versions.recitation.content) },
    keywords: { title: state.point.versions.keywords.title, content: toText(state.point.versions.keywords.content) },
  });
  const [expanded, setExpanded] = useState<Version>(initialVersion);

  const drawerTitle = state.mode === "keyword" ? "关键词回忆" : state.mode === "single" ? "单独编辑" : "整体编辑";
  const saveSingle = () => { if (state.mode === "single") onSaveSingle(state.point.id, state.version, { title: singleTitle.trim(), content: fromText(singleText) }); };
  const saveGlobal = () => onSaveGlobal(state.point.id, {
    original: { title: globalDraft.original.title.trim(), content: fromText(globalDraft.original.content) },
    recitation: { title: globalDraft.recitation.title.trim(), content: fromText(globalDraft.recitation.content) },
    keywords: { title: globalDraft.keywords.title.trim(), content: fromText(globalDraft.keywords.content) },
  });

  return (
    <div className="drawer-layer is-open">
      <button className="drawer-scrim" onClick={onClose} aria-label="关闭抽屉" />
      <aside className="keyword-drawer" role="dialog" aria-modal="true" aria-label={drawerTitle}>
        <div className="drawer-header"><div><span>{drawerTitle}</span><h2>{initialValue.title}</h2></div><button onClick={onClose} aria-label="关闭"><Icon name="close" /></button></div>
        <div className="drawer-body">
          {state.mode === "keyword" && <>
            <p className="drawer-context">对应背诵版内容</p><div className="drawer-rule" />
            {state.point.versions.recitation.content.map((text, index) => <p className="drawer-reading" key={index}>{text}</p>)}
          </>}
          {state.mode === "single" && <>
            <p className="drawer-context">{versionLabels[state.version]}</p><div className="drawer-rule" />
            <label className="edit-label" htmlFor="single-title">标题</label>
            <input className="edit-title-input" id="single-title" value={singleTitle} onChange={(event) => setSingleTitle(event.target.value)} />
            <label className="edit-label" htmlFor="single-edit">内容</label>
            <textarea id="single-edit" value={singleText} onChange={(event) => setSingleText(event.target.value)} />
            <div className="drawer-actions"><button onClick={onClose}>取消</button><button className="primary" onClick={saveSingle}>保存修改</button></div>
          </>}
          {state.mode === "global" && <>
            <p className="drawer-context">同一要点的三个版本</p><div className="drawer-rule" />
            <div className="accordion">
              {(["original", "recitation", "keywords"] as Version[]).map((version) => (
                <section className={expanded === version ? "is-expanded" : ""} key={version}>
                  <button className="accordion-trigger" onClick={() => setExpanded(version)} aria-expanded={expanded === version}><span>{versionLabels[version]}</span><i>⌄</i></button>
                  {expanded === version && <div className="accordion-fields">
                    <label className="edit-label" htmlFor={`${version}-title`}>标题</label>
                    <input className="edit-title-input" id={`${version}-title`} aria-label={`${versionLabels[version]}标题`} value={globalDraft[version].title} onChange={(event) => setGlobalDraft({ ...globalDraft, [version]: { ...globalDraft[version], title: event.target.value } })} />
                    <label className="edit-label" htmlFor={`${version}-content`}>内容</label>
                    <textarea id={`${version}-content`} aria-label={`${versionLabels[version]}内容`} value={globalDraft[version].content} onChange={(event) => setGlobalDraft({ ...globalDraft, [version]: { ...globalDraft[version], content: event.target.value } })} />
                  </div>}
                </section>
              ))}
            </div>
            <div className="drawer-actions"><button onClick={onClose}>取消</button><button className="primary" onClick={saveGlobal}>保存全部</button></div>
          </>}
        </div>
      </aside>
    </div>
  );
}
