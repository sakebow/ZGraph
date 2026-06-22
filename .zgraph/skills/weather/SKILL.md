---
name: weather
description: Get current weather and forecasts (no API key required).
homepage: https://wttr.in/:help
emoji: 🌤️
icon: https://wttr.in/icon.png
required_tools: bash, read, datetime
validations: merged-output-created
tags: weather, forecast, temperature, wind
---

# 天气查询技能

## 日期工具要求

凡是涉及“今天、明天、后天、当前时间”等相对日期，必须先调用 `datetime` 工具获取当前日期。
例如查询今天时，调用 `datetime(format="YYYY-MM-DD")`；不要根据模型上下文猜日期，也不要用 `date`、`Get-Date` 等 shell 命令获取日期。

只要用户问"天气""气温""预报""今天下雨吗""明天几度"之类的话，就用这个技能来查天气。

两个免费服务，不需要 API 密钥。

## wttr.in（主要来源）

快速一行输出：
```bash
curl -s "wttr.in/London?format=3"
# Output: London: ⛅️ +8°C
```

紧凑格式：
```bash
curl -s "wttr.in/London?format=%l:+%c+%t+%h+%w"
# Output: London: ⛅️ +8°C 71% ↙5km/h
```

完整预报：
```bash
curl -s "wttr.in/London?T"
```

常用参数：
- `%c` 天气状况 · `%t` 温度 · `%h` 湿度 · `%w` 风力 · `%l` 地点 · `%m` 月相
- 空格要 URL 编码：`wttr.in/New+York`
- 机场代码：`wttr.in/JFK`
- 单位：`?m`（公制）`?u`（英制）
- 只看今天：`?1` · 只看当前：`?0`
- 输出图片：`curl -s "wttr.in/Berlin.png" -o /tmp/weather.png`

## Open-Meteo（备用，返回 JSON）

免费，不需要密钥，适合程序化使用：
```bash
curl -s "https://api.open-meteo.com/v1/forecast?latitude=51.5&longitude=-0.12&current_weather=true"
```

使用方式：先找到城市的经纬度，再查询。返回 JSON 格式的温度、风速、天气代码。

文档：https://open-meteo.com/en/docs

## 回复风格

查询结果可以自然总结，也可以按日期整理成 Markdown 表格展示：

```markdown
| 日期 | 天气 | 温度 | 风力 |
|---|---|---|---|
| 周一 | 晴 | 22°C | 3km/h |
| 周二 | 多云 | 19°C | 5km/h |
```
