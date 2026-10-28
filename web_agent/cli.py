"""命令行入口：python -m web_agent.cli --task '调查任务'"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from .persistence import redact
from .security import safe_identifier

from .config import Settings
from .runner import run_task


def doctor() -> int:
    settings = Settings.load()
    print("Python:", sys.version.split()[0])
    print("Node npx:", shutil.which("npx.cmd") or shutil.which("npx") or "未找到")
    print("DeepSeek key:", "已配置" if settings.api_key else "未配置")
    print("DeepSeek model:", settings.model)
    print("输出目录:", settings.output_dir)
    return 0 if settings.api_key and (shutil.which("npx.cmd") or shutil.which("npx")) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="WebTask Agent - 公开网页多工具自主执行")
    parser.add_argument("--task", type=str, help="使用自然语言描述任务")
    parser.add_argument("--thread-id", type=str, default=None, help="可选，复用会话 ID")
    parser.add_argument("--doctor", action="store_true", help="检查本机运行条件")
    parser.add_argument("--resume", action="store_true", help="显式从同一 thread ID 恢复")
    parser.add_argument("--decision", choices=['reject','handled','approve'], help="拒绝、人工已处理，或批准具体普通表单")
    parser.add_argument('--approval-id',default='',help='approve必须绑定待审批结果中的approval_id')
    parser.add_argument("--note", default='', help="人工审核说明")
    parser.add_argument("--unlock", action='store_true', help="确认旧进程已退出后移除该任务的残留锁")
    args = parser.parse_args()
    if args.doctor:
        raise SystemExit(doctor())
    if args.unlock:
        if not args.thread_id: parser.error('--unlock 需要 --thread-id')
        (Settings.load().output_dir/safe_identifier(args.thread_id)/'.running').unlink(missing_ok=True)
        print('残留锁已清理。')
        return
    if args.resume and not args.thread_id: parser.error('--resume 需要 --thread-id')
    if args.decision and not args.resume: parser.error('--decision 需要 --resume')
    if args.decision=='approve' and not args.approval_id:parser.error('approve需要--approval-id')
    if not args.task and not args.resume:
        parser.error("请传入 --task 或 --doctor")
    result = asyncio.run(run_task(args.task or '', args.thread_id,resume=args.resume,
                                 decision={'decision':args.decision,'note':args.note,'approval_id':args.approval_id} if args.decision else None))
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    if result.status in {"error","timeout","budget_exceeded","incomplete",'partial','stalled'}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
