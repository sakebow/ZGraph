---
name: weather
description: 查询城市当前天气，支持 wttr.in 与 OpenWeather 两种数据源。
required_tools:
  - http_get
tags:
  - weather
  - http
  - lookup
---

# Weather

回答用户关于"今天北京天气怎么样""上海气温多少度"之类的问题。

## 数据源

主源：[wttr.in](https://wttr.in)，无 key 直接用 HTTP GET 即可。

回退：OpenWeather API（需要 `OPENWEATHER_API_KEY`）。

## 使用方式

1. 解析用户输入里的城市名（中文转拼音或英文）。
2. 调 wttr.in 拿原始 JSON 或纯文本。
3. 把温度数字透传给用户，**不要用 `%t` 这类占位符替代**。

## wttr.in 示例

```
$ curl 'https://wttr.in/Beijing?format=%l:+%t+%w'
Beijing: +8°C ↗19km/h
$ curl 'https://wttr.in/Shanghai?format=%l:+%t'
Shanghai: +14°C
```

`%t` 占位符展开后会是带正号的整数温度（如 `+8`、`-3`、`+14`），
回给用户时要直接复述数字，不要去掉 `+` 号。

## 异常

- wttr.in 超时 → 切 OpenWeather 回退
- 城市无法解析 → 提示用户给出英文城市名
