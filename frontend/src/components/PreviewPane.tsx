import { useEffect, useRef, useState } from "react";
import { X, PanelRightClose } from "lucide-react";
import {
  readPreviewWidth,
  savePreviewWidth,
  type PreviewTab,
} from "../viewState";
export type { PreviewTab } from "../viewState";
export default function PreviewPane({
  tab,
  onTab,
  onClose,
  children,
  hasPdf=false,
}: {
  tab: PreviewTab;
  onTab: (tab: PreviewTab) => void;
  onClose: () => void;
  children: React.ReactNode;
  hasPdf?: boolean;
}) {
  const [width, setWidth] = useState(readPreviewWidth);
  useEffect(() => savePreviewWidth(width), [width]);
  const pane = useRef<HTMLElement>(null);
  useEffect(() => {
    pane.current
      ?.querySelector<HTMLButtonElement>("button")
      ?.focus({ preventScroll: true });
  }, []);
  const [ceiling, setCeiling] = useState(900);
  useEffect(() => {
    const measure = () =>
      setCeiling(
        Math.max(
          300,
          (pane.current?.parentElement?.clientWidth ?? window.innerWidth) - 400,
        ),
      );
    const observer = new ResizeObserver(measure);
    if (pane.current?.parentElement)
      observer.observe(pane.current.parentElement);
    measure();
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector(".modal-overlay"))
        onClose();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  const resize = (value: number) =>
    setWidth(Math.max(300, Math.min(ceiling, value)));
  return (
    <aside
      id="work-preview"
      ref={pane}
      className="preview-pane"
      aria-label="文件与执行预览"
      style={{ width: Math.min(width, ceiling) }}
    >
      <div
        role="separator"
        tabIndex={0}
        aria-label="调整预览区宽度"
        aria-orientation="vertical"
        aria-valuemin={300}
        aria-valuemax={ceiling}
        aria-valuenow={Math.min(width, ceiling)}
        className="preview-resize"
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            e.preventDefault();
            resize(width + (e.key === "ArrowLeft" ? 40 : -40));
          }
        }}
        onPointerDown={(e) => {
          e.preventDefault();
          const target = e.currentTarget;
          target.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (e.currentTarget.hasPointerCapture(e.pointerId))
            resize(
              (pane.current?.getBoundingClientRect().right ?? innerWidth) -
                e.clientX,
            );
        }}
        onPointerUp={(e) => {
          if (e.currentTarget.hasPointerCapture(e.pointerId))
            e.currentTarget.releasePointerCapture(e.pointerId);
        }}
      />
      <header className="preview-header">
        <span>
          <PanelRightClose size={17} />
          文件与执行工作区
        </span>
        <button
          className="icon-button"
          aria-label="关闭预览区"
          onClick={onClose}
        >
          <X size={18} />
        </button>
      </header>
      <div role="tablist" aria-label="预览内容" className="preview-tabs">
        {(
          [
            { id: "report", label: "报告" },
            { id: "pdf", label: "PDF" },
            { id: "data", label: "Excel" },
            { id: "sources", label: "证据" },
            { id: "execution", label: "轨迹" },
            { id: "requirements", label: "验收" },
          ] as const
        ).filter(t => t.id !== "pdf" || hasPdf).map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            tabIndex={tab === t.id ? 0 : -1}
            id={`preview-tab-${t.id}`}
            aria-controls="preview-tab-content"
            onKeyDown={(e) => {
              const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
              if (!keys.includes(e.key)) return;
              e.preventDefault();
              const buttons = Array.from(
                e.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>(
                  '[role="tab"]',
                ),
              );
              const index = buttons.indexOf(e.currentTarget);
              const next =
                e.key === "Home"
                  ? 0
                  : e.key === "End"
                    ? buttons.length - 1
                    : (index +
                        (e.key === "ArrowRight" ? 1 : -1) +
                        buttons.length) %
                      buttons.length;
              buttons[next].click();
              buttons[next].focus();
            }}
            onClick={() => onTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div
        className="preview-body"
        id="preview-tab-content"
        role="tabpanel"
        aria-labelledby={`preview-tab-${tab}`}
        tabIndex={0}
      >
        {children}
      </div>
    </aside>
  );
}
