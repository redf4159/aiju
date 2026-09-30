# 场景设定与出图 Prompt（场景库）

## 场景列表

| ID | 名称 | 用途 |
|----|------|------|
| SC-01 | 刃雪城全景 | 开场、结尾、战争 |
| SC-02 | 冰凌宫大殿 | 朝会、对峙 |
| SC-03 | 冰镜禁室 | 预言、转折 |
| SC-04 | 城墙与冰阶 | 战斗、流血 |
| SC-05 | 幻雪神山 | 回忆、传说 |
| SC-06 | 潮雾边境 | 外敌、雾 |
| SC-07 | 彼岸花海（幻境） | 情感、结局 |
| SC-08 | 兄弟童年庭院 | 闪回 |

---

### SC-01 刃雪城全景

```
{STYLE_BASE}, aerial view of Xueren City, massive ice fortress,
spires and frozen walls, aurora sky, epic scale, empty, matte painting
```

### SC-02 冰凌宫大殿

```
{STYLE_BASE}, interior ice palace throne hall, crystal pillars,
frozen floor reflecting light, throne on ice steps, wide angle, no people
```

### SC-03 冰镜禁室

```
{STYLE_BASE}, secret chamber, ancient ice mirror standing in center,
blue glow, chains on walls, mysterious runes, low key lighting
```

### SC-04 城墙与冰阶

```
{STYLE_BASE}, city wall battlements, long ice stairs, snow storm,
banners fluttering, dramatic clouds
```

### SC-05 幻雪神山

```
{STYLE_BASE}, sacred snow mountain, temple ruins, blood red flowers sparse,
mist, holy atmosphere
```

### SC-06 潮雾边境

```
{STYLE_BASE}, border wasteland, gray toxic fog, dead trees,
silhouette of enemy army far away, desaturated
```

### SC-07 彼岸花海（幻境）

```
{STYLE_BASE}, surreal field of crimson spider lilies,
soft fog, dreamlike, fantasy afterlife aesthetic
```

### SC-08 兄弟童年庭院

```
{STYLE_BASE}, small courtyard in ice city, warm golden memory filter,
two child footprints in snow, soft focus, nostalgic
```

---

## 使用方式

- 空镜出图保存：`assets/scenes/SC-xx.png`  
- 分镜合成时：ControlNet 参考场景 + 人物 IP-Adapter 参考 CH-xx  
- 同一场景保持同一 SC 参考，仅改镜头景别（wide / medium / close）
