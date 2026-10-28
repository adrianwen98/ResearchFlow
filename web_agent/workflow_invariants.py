"""Deterministic execution safety checks; does not infer or certify facts."""
from __future__ import annotations
import copy


def redundant_delivery_inspection(journal, has_delivery):
    """A run of 3 same-state delivery inspections is not new research."""
    if not has_delivery or not isinstance(journal, dict):
        return False
    tail = list(journal.values())[-3:]
    return len(tail)==3 and all(isinstance(x,dict) and x.get('name')=='inspect_delivery'
                                and not x.get('error_code') for x in tail)


def cleared_delivery_failure(answer, requirements, check):
    """Clear only an outdated *delivery integrity* failure after last re-export.

    Never mark a research requirement as satisfied, even if files are valid.
    """
    if not (isinstance(answer,str) and answer.startswith('交付校验未通过：')
            and isinstance(check,dict) and check.get('artifacts_valid') is True
            and not check.get('issues')):
        return answer,requirements
    clean='交付文件经过重新导出，最终完整性校验已通过；研究事实与逐项需求仍需独立核验。'
    rows=copy.deepcopy(requirements)
    if not isinstance(rows,list):return clean,requirements
    for row in rows:
        if not isinstance(row,dict):continue
        result=row.get('result')
        if (isinstance(result,dict) and result.get('status')=='missing'
                and str(result.get('reason','')).startswith('交付校验未通过：')
                and '尚无完整逐项证据记录' in str(result.get('reason',''))):
            result['reason']='文件完整性校验已恢复；该需求仍缺乏完整逐项事实证据记录，不能视为已完成。'
    return clean,rows
