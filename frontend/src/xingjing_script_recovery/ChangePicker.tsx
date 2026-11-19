import { useMemo, useState } from "react";
import type { ScriptVersion } from "@/xingjing_creator_projects/types";
import { button, field } from "@/xingjing_source_planning/i18n";
import { useRecoveryTranslation } from "./i18n";
import { objects, type Action, type Change, type Item, type Part } from "./types";

export function ChangePicker({ source, base, disabled, changes, onAdd }: {
  source: ScriptVersion; base: ScriptVersion; disabled: boolean; changes: Change[]; onAdd: (change: Change) => void;
}) {
  const { t } = useRecoveryTranslation();
  const [part, setPart] = useState<Part>("paragraph"), [action, setAction] = useState<Action>("replace");
  const [sourceId, setSource] = useState(""), [targetId, setTarget] = useState("");
  const historical = useMemo(() => objects(source, part), [source, part]);
  const destinations = useMemo(() => objects(base, part, action === "insert"), [base, part, action]);
  const change: Change = { action, part, sourceId: action === "remove" ? null : sourceId, targetId };
  const duplicate = changes.some(c => c.action === action && c.part === part && c.targetId === targetId && c.sourceId === change.sourceId);
  const clear = () => { setSource(""); setTarget(""); };
  const pick = (items: Item[], isTarget: boolean) => {
    const value = isTarget ? targetId : sourceId, item = items.find(i => i.id === value);
    const anchor = isTarget && action === "insert";
    return <div className="min-w-0">
      <label className="block text-sm">{t(isTarget ? anchor ? "anchor" : "targetItem" : "sourceItem")}
        <select className={field} value={value} onChange={e => (isTarget ? setTarget : setSource)(e.target.value)}>
          <option value="">{t("choose")}</option>
          {anchor && part === "scene" ? <option value="@start">{t("start")}</option> : null}
          {items.map(i => <option key={i.id} value={i.id}>{i.id} · {t(anchor && part === "paragraph" && i.kind === "scene" ? "sceneStart" : i.kind)} · {i.label.slice(0, 80)}</option>)}
        </select>
      </label>
      {item ? <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/20 p-3 font-sans text-sm leading-6">{item.body}</pre> : null}
    </div>;
  };
  return <fieldset disabled={disabled} className="mt-4 space-y-4">
    <div className="grid gap-4 sm:grid-cols-2">
      <label className="text-sm">{t("part")}<select className={field} value={part} onChange={e => {
        const next = e.target.value as Part;
        setPart(next); if (next === "heading") setAction("replace"); clear();
      }}>{(["paragraph", "scene", "heading"] as const).map(k => <option key={k} value={k}>{t(k)}</option>)}</select></label>
      <label className="text-sm">{t("actionLabel")}<select className={field} value={action} onChange={e => {
        setAction(e.target.value as Action); clear();
      }}>{(part === "heading" ? ["replace"] : ["replace", "insert", "remove"]).map(k => <option key={k} value={k}>{t(k)}</option>)}</select></label>
    </div>
    <div className="grid gap-4 sm:grid-cols-2">
      {action !== "remove" ? pick(historical, false) : null}{pick(destinations, true)}
    </div>
    <button type="button" className={button} disabled={!targetId || action !== "remove" && !sourceId || duplicate || changes.length >= 100}
      onClick={() => { onAdd(change); clear(); }}>{t("add")}</button>
  </fieldset>;
}
