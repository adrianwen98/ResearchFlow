# 南极企鹅观测：样本结构与体重对比

生成日期：2026-10-26

数据范围：2026-10-26 保存的 3 条来源记录。

> 免费公开数据工具复现：决策顺序和报告模板由脚本给定，没有调用大模型；用于展示真实数据处理与交付，不作为Agent自主性验收。

## 数据质检
实际读取344条观测、8列；体重缺失2条，性别缺失11条。无静默删除坏行；均值仅使用非缺失体重。

## 样本对比
| 物种 | 样本数（含体重缺失） | 非缺失体重均值(g) |
|---|---:|---:|
| Gentoo | 124 | 5076.02 |
| Chinstrap | 68 | 3733.09 |
| Adelie | 152 | 3700.66 |

体重均值计算ID：calc_8bd9907c96ae。样本数不代表岛屿或物种的总体数量；观测数据不能据此得出因果关系。

## 用途与局限
可用来发现缺失记录、比较采集样本结构并检查分析口径。数据为2007–2009年观测样本，并非当前种群普查。读取范围为CSV最多5000行，本文件344行全部纳入；3组统计未触及前20组限制。

## 来源及许可
数据项目：https://allisonhorst.github.io/palmerpenguins/ 。原始CSV：https://raw.githubusercontent.com/allisonhorst/palmerpenguins/main/inst/extdata/penguins.csv 。项目声明数据以CC0提供。引用：Horst AM, Hill AP, Gorman KB (2020), palmerpenguins, doi:10.5281/zenodo.3960218。

## 数据口径与未核实事项

- Adelie、Gentoo、Chinstrap / 样本数：未核实。自动保守披露：需要真实source_id及连续原文quote

## 规范化数据与核验说明

下表与业务 Excel 使用同一份数据。来源、字段状态和规范化规则见 Provenance，限制及未核实事项见 Metadata。

### 物种样本数对比

| 对象 | 样本数 (count) | 核验说明 |
| --- | --- | --- |
| Adelie | 未核实 | 未核实：样本数 |
| Gentoo | 未核实 | 未核实：样本数 |
| Chinstrap | 未核实 | 未核实：样本数 |

---

## 已记录的来源与原文摘录

### 来源 1 · public_9a9e357562d0.csv 计算结果（calc_02c1b0885cff）

[查看原始页面](<https://raw.githubusercontent.com/allisonhorst/palmerpenguins/main/inst/extdata/penguins.csv>)

记录的事实：public_9a9e357562d0.csv 按 species 分组，对 行数 计算 count；样本 344 行，3 组。

原文摘录：{"calculation_id": "calc_02c1b0885cff", "group_by": "species", "method": "count", "numeric_column": "", "sampled_rows": 344, "total_groups": 3, "top_20": [{"group": "Adelie", "value": 152.0}, {"group": "Gentoo", "value": 124.0}, {"group": "Chinstrap", "value": 68.0}], "note": ""}

查阅时间：2026-10-26T16:03:16+00:00

### 来源 2 · public_9a9e357562d0.csv 计算结果（calc_8bd9907c96ae）

[查看原始页面](<https://raw.githubusercontent.com/allisonhorst/palmerpenguins/main/inst/extdata/penguins.csv>)

记录的事实：public_9a9e357562d0.csv 按 species 分组，对 body_mass_g 计算 mean；样本 344 行，3 组。

原文摘录：{"calculation_id": "calc_8bd9907c96ae", "group_by": "species", "method": "mean", "numeric_column": "body_mass_g", "sampled_rows": 344, "total_groups": 3, "top_20": [{"group": "Gentoo", "value": 5076.016260162602}, {"group": "Chinstrap", "value": 3733.0882352941176}, {"group": "Adelie", "value": 3700.662251655629}], "note": ""}

查阅时间：2026-10-26T16:03:16+00:00

### 来源 3 · Palmer Penguins公开观测样本

[查看原始页面](<https://raw.githubusercontent.com/allisonhorst/palmerpenguins/main/inst/extdata/penguins.csv>)

记录的事实：真实CSV结构与缺失值检查

原文摘录：{"sampled_rows": 344, "columns": ["species", "island", "bill_length_mm", "bill_depth_mm", "flipper_length_mm", "body_mass_g", "sex", "year"], "null_counts": {"species": 0, "island"

查阅时间：2026-10-26T16:03:16+00:00
