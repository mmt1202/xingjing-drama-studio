import { useEffect, useRef, useState } from "react";
import { Download, Scan, X } from "lucide-react";
import { useTurnaroundTranslation } from "./turnaround-i18n";
import "./artwork-viewer.css";

export function ArtworkViewer({ url, title, mime, onClose }: {
  url: string; title: string; mime?: string; onClose: () => void;
}) {
  const { t } = useTurnaroundTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const [scale, setScale] = useState<0 | 1 | 2>(0);
  const [size, setSize] = useState<{ width: number; height: number }>();
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  const extension = { "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/avif": ".avif" }[mime ?? ""];
  const fileName = title.replace(/[<>:"/\\|?*]/g, "-").slice(0, 120) + (extension ?? "");
  return <dialog ref={dialog} className="xj-art-viewer" aria-label={t("enlarge", { name: title })} onCancel={onClose}
    onKeyDown={(event) => {
      const shifts: Record<string, [number, number]> = { ArrowRight: [80, 0], ArrowLeft: [-80, 0], ArrowDown: [0, 80], ArrowUp: [0, -80] };
      const shift = shifts[event.key];
      if (scale && shift) { event.preventDefault(); viewport.current?.scrollBy(...shift); }
    }}>
    <header><div><strong>{title}</strong>{size && <span>{t("pixels", size)}</span>}</div>
      <div className="xj-art-tools">
        <div role="group" aria-label={t("zoomLevel")}>{([0, 1, 2] as const).map((step) =>
          <button key={step} type="button" aria-pressed={scale === step} onClick={() => setScale(step)} disabled={!size}>
            {step === 0 ? <><Scan size={15} />{t("fit")}</> : `${step * 100}%`}
          </button>)}</div>
        <a href={url} download={fileName}><Download size={16} />{t("downloadOriginal")}</a>
        <button type="button" onClick={onClose} aria-label={t("closeViewer")}><X size={19} /></button>
      </div>
    </header>
    <div ref={viewport} className="xj-art-viewport" data-fit={scale === 0} role="region" aria-label={t("imageDetail")}
      onPointerDown={(event) => {
        if (!scale || event.button !== 0 || event.pointerType === "touch") return;
        const pane = event.currentTarget;
        drag.current = { x: event.clientX, y: event.clientY, left: pane.scrollLeft, top: pane.scrollTop };
        pane.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (drag.current) event.currentTarget.scrollTo(drag.current.left + drag.current.x - event.clientX, drag.current.top + drag.current.y - event.clientY);
      }}
      onLostPointerCapture={() => { drag.current = null; }}>
      <div className="xj-art-plane"><img src={url} alt={title} draggable={false}
        onLoad={(event) => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
        style={scale && size ? { width: size.width * scale, height: size.height * scale } : undefined} /></div>
    </div>
    {scale > 0 && <footer>{t("panHint")}</footer>}
  </dialog>;
}
