# 柔和界面风 — 风格参考

> 大圆角、彩色柔光阴影、低饱和配色，让人第一眼就想点。

**Theme:** light

柔和界面风服务消费级产品：社交、生活方式、面向普通用户的工具。它的做法是把所有硬的东西变软——圆角从 16px 起、卡片 24px；阴影不只是暗，而是带主色相的彩色柔光（靛蓝 25% 透明度、24px 以上模糊）；颜色一律低饱和，纯黑和纯饱和色都被排除。交互靠浮起表达：hover 时元素上移、阴影扩大、主色加深，按下时先落回再轻微内凹。动效允许慢到 300 至 500ms，因为柔和本身就是被感知的时长。

## Colors

| Name | Value | Token | Role |
|---|---|---|---|
| Indigo | `#6366f1` | `--color-indigo` | 主行动、选中态与链接。柔和不等于没有主色，它需要一支明确的品牌色。 |
| Mist | `#f1f5f9` | `--color-mist` | 页面画布与输入框底。比白更柔，让白色卡片有依托。 |
| Cloud White | `#ffffff` | `--color-cloud` | 卡片与浮层填充。柔和风格里最亮的一层，但不刺眼，因为阴影把它托起来。 |
| Blossom | `#ec4899` | `--color-blossom` | 情绪化强调：收藏、喜欢、社区反馈。不与靛蓝同时作为主色。 |
| Mint | `#10b981` | `--color-mint` | 成功与完成状态。用于进度、勾选、已保存。 |
| Amber | `#f59e0b` | `--color-amber` | 提醒与待办。出现频率最低，靠面积小来控制存在感。 |

## Typography

### Inter — 全站唯一字体。字重 600 承担亲和型标题，700 只用于数字与强调。

- **Substitute:** Nunito, -apple-system, 'Segoe UI', sans-serif
- **Weights:** 400, 500, 600, 700
- **Sizes:** 12, 14, 16, 20, 28, 36
- **Line height:** 1.2 (display), 1.3 (headings), 1.65 (body)
- **Letter spacing:** -0.01em on display, 0.02em on uppercase labels

### Type Scale

| Role | Family | Weight | Size | Line Height | Letter Spacing | Token |
|---|---|---|---|---|---|---|
| display | — | 700 | 36px | 1.2 | -0.01em | `--text-display` |
| heading | — | 600 | 28px | 1.3 | — | `--text-heading` |
| subheading | — | 600 | 20px | 1.4 | — | `--text-subheading` |
| body-lg | — | 400 | 16px | 1.65 | — | `--text-body-lg` |
| body | — | 400 | 14px | 1.65 | — | `--text-body` |
| label | — | 500 | 12px | 1.4 | 0.02em | `--text-label` |

## Spacing & Layout

**Base unit:** 8px
**Density:** airy

### Spacing Scale

| Name | Value | Token |
|---|---|---|
| 8 | 8px | `--spacing-8` |
| 16 | 16px | `--spacing-16` |
| 24 | 24px | `--spacing-24` |
| 32 | 32px | `--spacing-32` |
| 48 | 48px | `--spacing-48` |
| 64 | 64px | `--spacing-64` |
| 96 | 96px | `--spacing-96` |

### Border Radius

| Element | Value |
|---|---|
| cards | 24px |
| buttons | 16px |
| inputs | 16px |
| icons | 999px |

- **Page max-width:** 1200px
- **Section gap:** 96px
- **Card padding:** 32px
- **Element gap:** 24px

## Components

### Soft Button

**Role:** 主行动按钮

靛蓝填充、白字、16px 圆角、无描边。字级 16px、字重 500、padding 12px 24px。静态阴影为 0 10px 24px 靛蓝 25%。hover 上浮 4px、阴影扩到 0 18px 32px 35%；按下回落并换成 inset 0 4px 10px 主色深 28%，同时缩放 0.96。过渡 300ms ease-in-out。

- **Sizes:** padding 12px 24px; label 16px
- **States:** hover: lift 4px, shadow up; active: inset shadow, scale 0.96

### Soft Card

**Role:** 功能卡与内容卡

白底、24px 圆角、无描边、内边距 32px，阴影 0 12px 32px 石板灰 25%。图标块 56px 见方、16px 圆角、靛蓝 10% 底；hover 时卡片上浮 8px、阴影转靛蓝 20%、图标块放大 1.1 且底色变浅。过渡 500ms。

- **States:** hover: lift 8px, shadow tinted indigo, icon 1.1

### Soft Input

**Role:** 表单输入

雾灰底、无描边、16px 圆角、padding 14px 20px。聚焦时底色转白、加一圈 4px 靛蓝 20% 的外环，并补一层 0 10px 26px 靛蓝 14% 的柔光。过渡 300ms，不改变尺寸。

- **States:** focus: white fill, 4px ring, soft glow

### Icon Chip

**Role:** 功能图标与状态标记

圆形（999px）、40px 或 56px 直径、主色 10% 底、图标用主色本身。不做边框与阴影，靠底色区分。hover 时底色升到 16%，图标不动。

## Do's and Don'ts

### Do

- 圆角 16px 起，卡片 24px
- 阴影带主色相而不是纯黑，模糊 24px 以上
- 配色一律低饱和，排除纯黑与纯饱和色
- hover 上浮 4px 到 8px，按下回落并内凹
- 卡片间距至少 24px，宁可减少模块
- 动效 300ms 起，500ms 用于卡片级位移

### Don't

- 不要用直角或小于 16px 的圆角
- 不要用纯黑文字 #000000
- 不要用无模糊的硬阴影
- 不要用 1px 实色描边切分区块
- 不要用高饱和纯色做大面积填充
- 不要压缩间距换取信息密度

## Surfaces

| Level | Name | Value | Purpose |
|---|---|---|---|
| 0 | Mist | `#f1f5f9` | 页面画布 |
| 1 | Cloud White | `#ffffff` | 卡片与浮层 |

## Elevation

- **Card rest:** `0 12px 32px rgba(148,163,184,0.25)`
- **Card hover:** `0 24px 48px rgba(99,102,241,0.20)`
- **Button rest:** `0 10px 24px rgba(99,102,241,0.25)`
- **Button hover:** `0 18px 32px rgba(99,102,241,0.35)`
- **Button pressed:** `inset 0 4px 10px rgba(67,56,202,0.28)`

## Imagery

明亮、低饱和的摄影与 3D 柔光图形。人物可用，但必须温和、日常、无强对比。允许渐变色块作为背景柔光。不使用硬边插画、纯黑背景或高饱和摄影。

## Layout

12 栏、上限 1200px，容器内边距 24px。区块间距 96px，卡片间距 24px，元素间距 24px。留白是风格的一部分：宁可少放一个模块，也不要压缩间距。卡片可以错落，但每行不超过三张。

## Agent Prompt Guide

### Implementation Contract

- 以用户请求中的页面、用户任务和真实内容为依据，只实施指定范围；标明缺失的业务信息，不编造数据或能力。
- 先检查现有项目、技术栈与组件；沿用项目结构、路由和依赖，复用已有组件，不重建未要求的页面或功能。
- 沿用项目断点，调整窄屏与桌面的网格和留白；保留信息层级，避免意外横向溢出。
- 对本次涉及的交互，处理键盘焦点与减少动态效果；补齐适用的禁用、加载、空内容与错误状态。
- 完成后按项目现有脚本检查，仅报告实际运行的测试与视觉检查，并列出未验证项。

QUICK COLOR REFERENCE:

- text: `#1f2937`
- background: `#f1f5f9`
- card: `#ffffff`
- primary: `#6366f1`
- muted: `#64748b`
- accent: `#ec4899`

EXAMPLE COMPONENT PROMPTS:

1. 做一个柔和风格的功能卡：白底、24px 圆角、无边框、内边距 32px、阴影 0 12px 32px rgba(148,163,184,0.25)。左上放 56px 圆形图标块（靛蓝 10% 底、图标 #6366f1），下面是 20px/600 标题和 14px #64748b 正文。hover 时卡片上浮 8px、阴影改成 rgba(99,102,241,0.2)、图标放大 1.1，过渡 500ms。

## Similar Brands

- **Notion** — 同样的低饱和与宽松间距，但更克制，不用彩色柔光
- **Duolingo** — 大圆角与亲和型动效，但饱和度更高、更有游戏感

## Quick Start

```css

:root {
  /* Colors */
  --color-indigo: #6366f1;
  --color-mist: #f1f5f9;
  --color-cloud: #ffffff;
  --color-blossom: #ec4899;
  --color-mint: #10b981;
  --color-amber: #f59e0b;

  /* Typography — Font Families */
  --font-sans: 'Inter', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;

  /* Typography — Scale */
  --text-display: 36px;
  --leading-display: 1.2;
  --tracking-display: -0.01em;
  --text-heading: 28px;
  --leading-heading: 1.3;
  --text-subheading: 20px;
  --leading-subheading: 1.4;
  --text-body-lg: 16px;
  --leading-body-lg: 1.65;
  --text-body: 14px;
  --leading-body: 1.65;
  --text-label: 12px;
  --leading-label: 1.4;
  --tracking-label: 0.02em;

  /* Typography — Weights */
  --font-weight-regular: 400;
  --font-weight-medium: 500;
  --font-weight-semibold: 600;
  --font-weight-bold: 700;

  /* Spacing */
  --spacing-unit: 8px;
  --spacing-8: 8px;
  --spacing-16: 16px;
  --spacing-24: 24px;
  --spacing-32: 32px;
  --spacing-48: 48px;
  --spacing-64: 64px;
  --spacing-96: 96px;

  /* Layout */
  --page-max-width: 1200px;
  --section-gap: 96px;
  --card-padding: 32px;
  --element-gap: 24px;
  --density: airy;

  /* Border Radius */
  --radius-cards: 24px;
  --radius-buttons: 16px;
  --radius-inputs: 16px;
  --radius-icons: 999px;

  /* Surfaces */
  --surface-mist: #f1f5f9;
  --surface-cloud-white: #ffffff;
}

```
