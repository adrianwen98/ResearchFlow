"""Shared task states. Completed requires valid human or objective host checks."""
STATUS={
 'idle':('等待任务','neutral'),'queued':('等待执行','neutral'),'running':('正在执行','active'),
 'partial':('部分完成','warning'),'needs_review':('结果待确认','warning'),'completed':('已完成','success'),
 'awaiting_review':('等待操作审批','warning'),'permission_denied':('权限拒绝，已停止','warning'),
 'budget_exceeded':('执行预算耗尽','warning'),'stalled':('重复失败，已停止','warning'),
 'interrupted':('执行已中断','warning'),'timeout':('执行超时','warning'),
 'incomplete':('未完成','warning'),'error':('执行失败','error')}

def completion_outcome(status,has_sources):
    if status in {'completed','needs_review'}:return status
    if status in {'idle','queued','running','awaiting_review'}:return 'pending'
    return 'partial' if has_sources else 'failed'
