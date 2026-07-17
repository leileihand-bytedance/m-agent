# 资本市场数据提取

只从材料中提取明确列示的指数涨跌幅或起止收盘值，不计算、不倒算、不猜测缺失值。

必须完整返回四组：`weekly_a`、`monday_a`、`weekly_hk`、`weekly_us`。指数代码分别为：

- A股：000001、399001、399006
- 港股：HSI、HSTECH、HSCEI
- 美股：DJIA、COMP、SPX

每个 `evidence_excerpt` 必须逐字存在于对应网页正文；无法完整取值时返回现有项，由代码拒绝不完整结果。

`start_date` 和 `end_date` 统一使用 `YYYY-MM-DD`。具体统计期和出版日在本次任务指令中给出，不得把搜索结果发布日期当作指数交易日期。

- 页面直接列出本周或当日涨跌幅时，只填写 `reported_change_pct`，不填写 `start_close`、`end_close`；百分数下跌填写负数，例如“下跌1.17%”填 `-1.17`。
- `monday_a` 使用页面直接披露的周一当日涨跌幅时，`start_date`、`end_date` 都填写出版日。
- 页面明确列出起止两个收盘值时，只填写 `start_close`、`end_close`，不填写 `reported_change_pct`。
- 两种证据模式不能混用。最终涨跌描述和指数标准名称均由代码生成。
