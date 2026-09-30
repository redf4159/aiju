# 人物设定与出图 Prompt（定妆用）

## 全剧风格基底 STYLE_BASE

```
Chinese xianxia fantasy, cinematic lighting, ultra detailed, 8k,
ice and snow aesthetic, silver blue color palette, film still,
sharp focus, no modern clothing, no text, no watermark
```

## 角色列表

| ID | 姓名 | 身份 | 年龄感 | 服装主色 | 备注 |
|----|------|------|--------|----------|------|
| CH-01 | 卡索 | 刃雪城王 | 青年 | 白+银饰 | 黑发，眉宇深锁，王者气质 |
| CH-02 | 樱空释 | 王弟/幻术师 | 青年 | 白+暗纹 | 银灰发，眼神清澈又危险 |
| CH-03 | 蝶澈 | 巫医/歌者 | 青年 | 淡紫+白纱 | 长发，持笛或铃 |
| CH-04 | 皇北城 | 将军 | 壮年 | 玄甲+冰纹 | 刀疤可选，忠诚 |
| CH-05 | 潮雾使 | 反派使者 | 不明 | 灰雾斗篷 | 无面或半脸 |

---

### CH-01 卡索 · 定妆 Prompt

**中文简述**：青年帝王，黑长发束冠，白狐裘或冰纹长袍，佩长剑，表情克制疲惫。

**英文 Prompt（ComfyUI）**：
```
{STYLE_BASE}, portrait of Kasuo, young Chinese fantasy king,
black long hair with silver crown, white fur trimmed robe,
ice pattern embroidery, cold noble expression, holding sword hilt,
snowflakes in background, bust shot, front view, character sheet
```

**Negative**：`modern suit, cartoon, low quality, blurry, extra fingers`

---

### CH-02 樱空释 · 定妆 Prompt

**中文简述**：银灰长发，白衣带暗纹，笑靥温和，眼底隐藏疯狂。

**英文 Prompt**：
```
{STYLE_BASE}, portrait of Ying Kong Shi, young man silver gray long hair,
white robe with dark subtle patterns, gentle smile but hollow eyes,
fantasy prince, soft backlight, bust shot, 3/4 view, character sheet
```

---

### CH-03 蝶澈 · 定妆 Prompt

**中文简述**：紫白纱衣，长发及腰，持银笛，气质清冷哀婉。

**英文 Prompt**：
```
{STYLE_BASE}, portrait of Die Che, beautiful young woman,
long black hair, light purple and white gauze dress,
holding silver flute, melancholic eyes, snow temple background blur,
bust shot, front view, character sheet
```

---

### CH-04 皇北城 · 定妆 Prompt

```
{STYLE_BASE}, portrait of Huang Beicheng, veteran general,
short hair, ice pattern black armor, scar on cheek,
loyal stern face, bust shot, character sheet
```

---

### CH-05 潮雾使 · 定妆 Prompt

```
{STYLE_BASE}, hooded envoy of mist, gray cloak, face half hidden,
glowing pale eyes, smoke tendrils, full body, ominous, character sheet
```

---

## 定妆出图顺序建议

1. CH-01 卡索（镜头最多，先定）  
2. CH-02 樱空释（与卡索同框多，发色/服装对比要强）  
3. CH-03 蝶澈  
4. CH-04、CH-05  

每张定妆图保存为：`assets/characters/CH-xx_name_ref.png`，分镜 Prompt 中写 `same face as CH-xx ref`。
