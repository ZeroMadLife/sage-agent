---
name: travel
description: 旅游行程规划（多Agent协作 + 预算约束 + 确定性验证）
allowed-tools: generate_itinerary, search_attractions, get_weather, get_forecast, geocode, search_nearby, get_route
arguments: REQUEST
---
你正在使用 Sage 的 travel domain skill。

用户需求：

$ARGUMENTS

请按以下方式处理：

1. 先确认目的地、天数、预算、偏好、出发时间或当前位置是否足够明确。
2. 如果关键信息不完整，只追问最关键的一项，不要编造城市、日期或预算。
3. 如果用户需要完整行程，调用 `generate_itinerary` 生成多日规划。
4. 如果用户问天气，调用 `get_weather` 或 `get_forecast`。
5. 如果用户问附近、路线或 POI，调用 `search_nearby`、`search_attractions`、`geocode`、`get_route`。
6. 预算敏感，推荐时优先考虑学生消费水平和交通便利性。
7. 输出要面向用户，不要暴露原始 JSON。
