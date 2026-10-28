# WebTask React 工作台

React + TypeScript + Vite；API 在 Python 项目 `web_agent/api.py`。请从根目录使用 `setup_web_windows.bat` 安装、`run_web_windows.bat` 启动。完整接口、安全与验证边界见 `../docs/WEB_WORKBENCH.md`。

开发：先启动 127.0.0.1:8000 的 FastAPI，再运行 `npm ci --ignore-scripts` 和 `npm run dev`（127.0.0.1:5173）。构建 `npm run build`；类型检查 `npm run typecheck`；免费前端测试 `npm test`。务必设置 npm 缓存到项目 D 盘 work 目录。前端不接收任何模型密钥，不使用静态 Mock 响应作为真实结果。
