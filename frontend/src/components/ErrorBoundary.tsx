import { Component, type ErrorInfo, type ReactNode } from "react";
import { RefreshCw, TriangleAlert } from "lucide-react";

export function ErrorFallback({ message }: { message?: string }) {
  return (
    <main className="app-crash" role="alert">
      <div className="app-crash-card">
        <span className="app-crash-icon">
          <TriangleAlert size={26} />
        </span>
        <h1>页面出现异常</h1>
        <p>当前任务和已保存的数据没有受影响。刷新页面即可继续。</p>
        {message && <code>{message}</code>}
        <button className="primary" onClick={() => window.location.reload()}>
          <RefreshCw size={16} />
          刷新页面
        </button>
      </div>
    </main>
  );
}

export default class ErrorBoundary extends Component<
  { children: ReactNode },
  { error?: Error }
> {
  state: { error?: Error } = {};

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ResearchFlow UI crashed", error, info);
  }

  render() {
    if (this.state.error) return <ErrorFallback message={this.state.error.message} />;
    return this.props.children;
  }
}
