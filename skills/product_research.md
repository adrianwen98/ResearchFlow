# 商品与参数比较
- 确定对比型号和具体版本，避免将不同存储容量、发布年份或地区版混为一谈。
- 优先使用官方产品页；记录单位、币种、适用国家和查阅日期。
- 参数找不到时标为“未查到”，不要猜测。
- 逐条调用 record_source，保留原文摘录；先保存依据，再做比较。
- 参数比较表用export_business_excel的comparison模式：自定业务columns，每行label为型号，cells每列给value、source_id及原文quote；未查到用null和reason。只支持原文直接出现的值，单位/地区放入列名；不要捏造转换结果。export_excel另存来源证据，文字报告用export_report。

- 业务比较表的非空value须直接出现在对应quote中，不能把概括性岗位方向、站点名称、推断的适用对象当作原文。URL自动附在Provenance；缺证据用null及reason。导出失败先按issues的表名/行号/列名逐项修正，不要整表反复改写。

- 业务表失败诊断若带draft_id，优先repair_business_excel只改错误格；entry仍需对应原文，不要每次重写全部工作表。
