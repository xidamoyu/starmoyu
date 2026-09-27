# DESIGN.md — starmoyu 前端组件契约

> auteur system register。多屏产品（对话 / 台账 / 达人 / 审批 / 导入），无 peak，靠一致性取胜。
> 所有屏幕共享同一套令牌与组件状态配方。新增变体先改本文件。

## 产品一句话
MCN 商务的**工作工具**：高频、密集、要可信。不是营销页，是每天都要用的内部系统。
场景：办公桌前、自然光、中密度信息流、要一眼扫出状态。

## 色板（OKLCH，restrained + 一 committed 主色）
- **背景** 纸白 `#f7f6f2`（mean L≈0.95，非暖米色带——chroma 近 0，朝品牌 hue 微染）
- **表面** 纯白 `#ffffff`（卡片/气泡/抽屉）
- **墨色** `#1d2733`（正文，对比度 ≥7:1）
- **主色** 深海蓝绿 `oklch(0.46 0.09 215)` ≈ `#0f4c5c`（committed：导航选中、主按钮、链接、当前态）
- **主色亮面** `oklch(0.93 0.02 210)` ≈ `#e2edef`（选中底、高亮）
- **成功/警告/危险** 语义色（绿 `#2f7d4f` / 琥珀 `#b07d1e` / 红 `#b4443a`），仅状态用
- 灰阶文本用墨色 55%/70% 透明度，不用纯灰（保持 hue 一致）

## 字体（humanist sans，非 Inter）
- 全局 `Avenir Next / Segoe UI / PingFang SC / Microsoft YaHei` —— humanist、开 aperture、中文回退干净
- 单族 3 字重（400/500/700），数字 `tabular-nums`（表格预算/ROI 对齐）
- display ≤ 22px / 正文 14px / 辅助 12px

## 组件清单 + 变体预算（systemscan 依据）
| 组件 | 变体 | 状态配方 |
|---|---|---|
| button | primary / ghost / danger（3，超预算即 drift） | hover 提亮 4% · active 按下 translate-y 1px · focus-visible 2px 主色外环 · disabled 40% 透明+原因 |
| badge·stage | 需求沟通/提案/签约/执行/结案/丢单 + 默认（7） | 彩色点 + 低饱和 tint 底，不用纯色块 |
| nav-item | default / active（2） | active=主色亮面底+主色字+4px 左指示 |
| table | 数据表（1） | sticky 表头 · tabular-nums · 斑马纹 `#faf9f6` · hover 行亮 |
| drawer | 右侧滑出（1） | 240–360ms ease-out · focus trap · esc 关闭 |
| card | 平铺卡（1） | 1px 全边框（禁单侧色条 accent） |
| modal-form | 弹窗表单（1） | 遮罩 40% · 表单标签左对齐 |
| timeline | 商单路线图（1） | 节点彩色点 + 连接线 · 当前节点外环 |

## 网格破坏（每屏一个，named）
全局：**对话页的助手气泡全宽穿透**（表格/卡片不受气泡 padding 限制，内容物可读性优先）——这是本系统的签名。

## 动效预算（≤1 family，app 只要过渡）
仅 **位移/淡入**（opacity + translate-y ≤6px），时长 120–180ms ease-out。
禁：每屏同种淡入上滑（变化表达）、scroll listener（用 IO/transition）、scale(0) 入场。
reduced-motion：降为 0–60ms 淡入或瞬时。

## House tells 打破记录（§7）
1. **非近黑**：背景 L≈0.95 的亮页，戏剧感放在投影与材质对比，不在暗色。
2. **无主色=琥珀**：用深海蓝绿（hue 215，来自商务/合同/账本的冷信任色，非 palette 舒适区）。
3. 附加：**状态条 header 打破** —— 对话页顶部不放 logo 栏，让输入框区自己承载品牌。

## 截图能力诚实标注
视觉抽取（截图→特质）依赖的真实模型能力**未验证可用**，后端置灰探测。
前端上传截图仅作为附件预览 + 提交到 `POST /api/deals/{id}/close` 的 `traits` 留档字段，
抽取 UI 隐藏，不假装能抽。能力就绪后后端开 `/vision/enabled` 即一键启用。

## 验收
slopscan 通过 · shoot 每屏 390/768/1440 目检 · 无 JS 时正文仍可读 · 焦点态可见。
