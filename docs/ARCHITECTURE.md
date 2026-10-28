# 当前架构：独立 ResearchFlow 工作台

```mermaid
flowchart LR
    User[用户目标 / 验收 / 审批] --> React[React + TypeScript 工作台]
    React -->|REST 创建 / 恢复 / 审批 / 下载| API[FastAPI 本地单进程]
    API -->|SSE 真实轨迹 + REST 状态| React
    API --> History[任务名称 / 归档 / 删除标记与分项清理]
    API --> Runner[现有 Task Runner]
    Gradio[Gradio 备用入口] --> Runner
    Runner --> Graph[LangGraph decide → execute → verify]
    Graph --> Harness[预算 / 权限 / 超时 / 分类重试 / 循环控制]
    Harness --> MCP[Playwright MCP 浏览器]
    MCP --> Egress[按会话临时出口：全量 DNS 检查 + 固定公网 IP]
    Files --> Egress
    Harness --> Files[PDF / CSV / XLSX / 报告工具]
    Graph <--> CP[SQLite Checkpoint / interrupt]
    Graph --> Context[Skills 按需加载 / Memory / 上下文裁剪]
    Files --> Artifacts[真实交付文件 + 来源与需求校验]
    Artifacts --> Review[当前版本绑定：用户验收或明确客观契约独立核验]
    Review --> API
    Runner --> Persist[原有任务文件 / 用量 / 脱敏日志视图]
    Persist --> API
```

React 不参与模型决策，不维护第二套任务状态机；界面操作能力由后端实际 Checkpoint、进程锁、审批、预算和交付核验决定。新增接口和逐项边界见 [WEB_WORKBENCH.md](WEB_WORKBENCH.md)。下文为保留的 Agent 核心技术说明。

---

# 技术架构与状态契约

```mermaid
flowchart TD
 UI[Gradio / FastAPI / CLI] --> Runner[任务生命周期：锁、累计预算、超时]
 Runner --> Decide[LangGraph decide：模型选择工具]
 Decide --> Execute[execute：权限、分类重试、循环检测、journal]
 Execute --> Browser[真实Playwright MCP：搜索、点击、筛选、下载]
 Execute --> Files[PDF / CSV / XLSX / pandas / 文件交付]
 Execute --> Approval[敏感操作 interrupt / 显式审批]
 Approval --> Execute
 Browser --> Decide
 Files --> Decide
 Decide --> Verify[来源 + 文件 + 逐项需求核验]
 Verify -->|有缺失| Partial[partial / incomplete]
 Verify -->|需判断| Review[needs_review：结果待确认]
 Verify -->|用户客观契约全部独立核验通过| Complete
 Review --> Accept[当前版本绑定 + 全部需求通过 + 用户确认]
 Accept --> Complete[completed：已完成]
 Runner <--> SQLite[SQLite Checkpoint / 本地任务目录]
```

- 图控制结构固定，URL、工具及调整路径由模型根据观察选择；公开企鹅案例另有脚本复现标识。
- `completion._accept_delivery`同时检查版本、真实文件、全部需求检查和当前状态。缺失需求不能靠勾选全部ID绕过，也不写入长期记忆。
- `current_result`会使旧的无效完成状态失效。文件或需求改变后须重新核验。
- 公共状态统一在`web_agent/status.py`。queued等待队列、running执行、awaiting_review等待操作审批、needs_review结果待确认、completed已完成、partial部分、incomplete未完成、permission_denied权限停止、budget_exceeded预算耗尽、stalled循环停止、timeout超时、interrupted中断、error异常。
- 只有completed点亮最后一步；needs_review停在人工验收。操作审批与交付验收是两个不同阶段。
- Memory只保存用户明确偏好和已验收摘要；Skills按需加载；上下文按字符与完整消息组裁剪，不宣称语义事实保证。
- 原生浏览器重启仅重建最后URL，旧ref/Cookie/表单现场不承诺恢复；文件和图状态持久保存。模糊外部副作用不自动重发。
- 公开目录不含原始Checkpoint和Memory。脱敏历史演示只读，新任务仍可在本机生成自己的Checkpoint。

最终网络出口、自动完成的保守条件与最新185/21项测试见 [最终稳定性记录](FINAL_STABILITY.md)。
