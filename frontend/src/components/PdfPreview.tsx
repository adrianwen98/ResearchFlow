import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, ExternalLink, FileText, LoaderCircle } from "lucide-react";
import { request } from "../api";

export default function PdfPreview({taskId,available}:{taskId:string;available:boolean}) {
  const [pages,setPages]=useState(0),[page,setPage]=useState(1),[zoom,setZoom]=useState("fit"),[error,setError]=useState("");
  useEffect(() => {
    let current=true;setPages(0);setPage(1);setError("");
    if (available) request<{pages:number}>(`/tasks/${encodeURIComponent(taskId)}/report-pdf`).then(result => {if(current)setPages(result.pages);}).catch(e => {if(current)setError(String(e.message));});
    return () => {current=false;};
  },[taskId,available]);
  if(!available)return <p>此任务尚未生成 PDF 文件。</p>;
  return <div className="pdf-workspace">
    <div className="pdf-toolbar" aria-label="PDF阅读工具">
      <span className="pdf-format-label"><FileText size={16}/> PDF</span>
      <div className="pdf-pagination">
        <button aria-label="PDF上一页" disabled={page<=1} onClick={() => setPage(p=>p-1)}><ChevronLeft size={17}/></button>
        <select aria-label="PDF页码" value={page} onChange={e => setPage(Number(e.target.value))} disabled={!pages}>
          {Array.from({length:pages||1},(_,i) => <option key={i} value={i+1}>{i+1} / {pages||"…"}</option>)}
        </select>
        <button aria-label="PDF下一页" disabled={page>=pages} onClick={() => setPage(p=>p+1)}><ChevronRight size={17}/></button>
      </div>
      <select aria-label="PDF缩放" value={zoom} onChange={e => setZoom(e.target.value)}><option value="fit">适应宽度</option><option value="100">100%</option></select>
      <a href={`/tasks/${encodeURIComponent(taskId)}/files/research_report.pdf?inline=true`} target="_blank" rel="noreferrer" aria-label="在新窗口打开PDF"><ExternalLink size={16}/></a>
    </div>
    <div className={`pdf-canvas ${zoom==="100"?"actual-size":""}`} tabIndex={0} aria-label={`PDF第${page}页，可滚动阅读`} onKeyDown={e => {
      if(e.key==="ArrowRight"&&page<pages){e.preventDefault();setPage(page+1);}
      if(e.key==="ArrowLeft"&&page>1){e.preventDefault();setPage(page-1);}
    }}>
      {error ? <p role="alert">PDF 预览失败：{error}。可从上方打开或下载原文件。</p> : !pages ? <p className="pdf-loading" role="status"><LoaderCircle className="spin" size={20}/> 正在读取PDF页面…</p> :
        <img key={`${taskId}:${page}`} src={`/tasks/${encodeURIComponent(taskId)}/report-pdf/pages/${page}`} alt={`实际PDF第${page}页`} onError={() => setError("页面图片无法加载")}/>}
    </div>
    <p className="pdf-page-caption">实际文件预览 · {pages ? `第 ${page} 页，共 ${pages} 页` : "正在加载"}</p>
  </div>;
}
