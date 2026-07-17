# 资本市场数据提取

只从材料中提取明确列示的指数起止收盘值，不计算涨跌幅，不猜测缺失值。

必须完整返回四组：`weekly_a`、`monday_a`、`weekly_hk`、`weekly_us`。指数代码分别为：

- A股：000001、399001、399006
- 港股：HSI、HSTECH、HSCEI
- 美股：DJIA、COMP、SPX

每个 `evidence_excerpt` 必须逐字存在于对应网页正文；无法完整取值时返回现有项，由代码拒绝不完整结果。
