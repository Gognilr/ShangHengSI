---
version: alpha
name: 商衡SI · 澄明二版「衡线」
description: 局域网内的商务文件安全初审工具。令牌取值以 frontend/src/styles/theme.css 为准，本文件只是 V36-F-00 的摘要，供工具核对对比度；两者不一致时以 theme.css 为准。深色外壳中的半透明浅底已先叠到所在表面上换算成十六进制。
colors:
  canvas: "#F3F6F9"
  surface: "#FFFFFF"
  paper: "#FFFFFF"
  ink-1: "#0E1726"
  ink-2: "#334155"
  ink-3: "#5E6B7E"
  brand: "#078A76"
  brand-icon: "#10A58C"
  brand-soft: "#E6F7F2"
  brand-ink: "#05695B"
  ai: "#6D5BF6"
  ai-soft: "#F1EEFF"
  ai-ink: "#4F3DD8"
  warn-soft: "#FFF5DD"
  warn-ink: "#8F4F00"
  danger-soft: "#FFEEEF"
  danger-ink: "#B4232F"
  trip-idle: "#8794A8"
  k-org-bg: "#E8EFFF"
  k-org-ink: "#1E5FEF"
  k-person-bg: "#F3ECFF"
  k-person-ink: "#8340EF"
  k-contact-bg: "#E1F6FA"
  k-contact-ink: "#0B778C"
  k-id-bg: "#FFEEDF"
  k-id-ink: "#AB5414"
  k-addr-bg: "#EFF5DF"
  k-addr-ink: "#5E761A"
  k-amount-bg: "#FFF3D6"
  k-amount-ink: "#9A6104"
  k-other-bg: "#EEF1F5"
  k-other-ink: "#636D7C"
  dark-canvas: "#0B0F14"
  dark-surface: "#121820"
  dark-paper: "#161C24"
  dark-ink-1: "#EDF1F6"
  dark-ink-3: "#8E9AAB"
  dark-brand: "#14A88E"
  dark-brand-soft: "#162E31"
  dark-brand-ink: "#5ED6BF"
  dark-ai: "#8B7CFF"
  dark-ai-soft: "#242741"
  dark-ai-ink: "#B8AEFF"
  dark-warn-soft: "#322E23"
  dark-warn-ink: "#F7C766"
  dark-danger-soft: "#312128"
  dark-danger-ink: "#FF9296"
  dark-trip-idle: "#5E6A7B"
  dark-k-org-bg: "#263145"
  dark-k-org: "#7EA6FF"
  dark-k-person-bg: "#2E2E45"
  dark-k-person: "#B791FF"
  dark-k-contact-bg: "#1E343E"
  dark-k-contact: "#4CC6DE"
  dark-k-id-bg: "#372E2B"
  dark-k-id: "#FF9F57"
  dark-k-addr-bg: "#2B342C"
  dark-k-addr: "#A9C85A"
  dark-k-amount-bg: "#353128"
  dark-k-amount: "#F1B544"
  dark-k-other-bg: "#282F38"
  dark-k-other: "#9AA6B6"
  white: "#FFFFFF"
typography:
  hero:
    fontFamily: SI Sans
    fontSize: 40px
    fontWeight: 600
    lineHeight: 52px
    letterSpacing: -0.02em
  greet:
    fontFamily: SI Sans
    fontSize: 36px
    fontWeight: 600
    lineHeight: 44px
    letterSpacing: -0.02em
  page:
    fontFamily: SI Sans
    fontSize: 24px
    fontWeight: 600
    lineHeight: 32px
  block:
    fontFamily: SI Sans
    fontSize: 18px
    fontWeight: 600
    lineHeight: 26px
  card:
    fontFamily: SI Sans
    fontSize: 16px
    fontWeight: 600
    lineHeight: 24px
  read:
    fontFamily: SI Sans
    fontSize: 15px
    fontWeight: 400
    lineHeight: 28px
  body:
    fontFamily: SI Sans
    fontSize: 14px
    fontWeight: 400
    lineHeight: 22px
  aux:
    fontFamily: SI Sans
    fontSize: 13px
    fontWeight: 400
    lineHeight: 20px
  tag:
    fontFamily: SI Sans
    fontSize: 12px
    fontWeight: 500
    lineHeight: 16px
  code:
    fontFamily: SI Mono
    fontSize: 13px
    fontWeight: 400
    lineHeight: 20px
rounded:
  paper: 6px
  tag: 6px
  control: 10px
  card: 14px
  sheet: 18px
  pop: 16px
  composer: 24px
  badge: 999px
spacing:
  base: 4px
  card-pad: 20px
  card-gap: 16px
  section: 48px
  page-side: 40px
  page-side-narrow: 28px
  control: 36px
  control-sm: 30px
  control-lg: 44px
components:
  button-primary:
    backgroundColor: "{colors.brand}"
    textColor: "{colors.white}"
    rounded: "{rounded.control}"
    height: 36px
  button-ai:
    backgroundColor: "{colors.ai}"
    textColor: "{colors.white}"
    rounded: "{rounded.control}"
    height: 36px
  badge-brand:
    backgroundColor: "{colors.brand-soft}"
    textColor: "{colors.brand-ink}"
    rounded: "{rounded.badge}"
  badge-ai:
    backgroundColor: "{colors.ai-soft}"
    textColor: "{colors.ai-ink}"
    rounded: "{rounded.badge}"
  badge-warn:
    backgroundColor: "{colors.warn-soft}"
    textColor: "{colors.warn-ink}"
    rounded: "{rounded.badge}"
  badge-danger:
    backgroundColor: "{colors.danger-soft}"
    textColor: "{colors.danger-ink}"
    rounded: "{rounded.badge}"
  zone-local-aux:
    backgroundColor: "{colors.brand-soft}"
    textColor: "{colors.ink-3}"
    rounded: "{rounded.card}"
  zone-model-aux:
    backgroundColor: "{colors.ai-soft}"
    textColor: "{colors.ink-3}"
    rounded: "{rounded.card}"
  pill-org:
    backgroundColor: "{colors.k-org-bg}"
    textColor: "{colors.k-org-ink}"
    rounded: "{rounded.tag}"
  pill-person:
    backgroundColor: "{colors.k-person-bg}"
    textColor: "{colors.k-person-ink}"
    rounded: "{rounded.tag}"
  pill-contact:
    backgroundColor: "{colors.k-contact-bg}"
    textColor: "{colors.k-contact-ink}"
    rounded: "{rounded.tag}"
  pill-id:
    backgroundColor: "{colors.k-id-bg}"
    textColor: "{colors.k-id-ink}"
    rounded: "{rounded.tag}"
  pill-addr:
    backgroundColor: "{colors.k-addr-bg}"
    textColor: "{colors.k-addr-ink}"
    rounded: "{rounded.tag}"
  pill-amount:
    backgroundColor: "{colors.k-amount-bg}"
    textColor: "{colors.k-amount-ink}"
    rounded: "{rounded.tag}"
  pill-other:
    backgroundColor: "{colors.k-other-bg}"
    textColor: "{colors.k-other-ink}"
    rounded: "{rounded.tag}"
  dark-button-primary:
    backgroundColor: "{colors.dark-brand}"
    textColor: "{colors.white}"
    rounded: "{rounded.control}"
  dark-badge-ai:
    backgroundColor: "{colors.dark-ai-soft}"
    textColor: "{colors.dark-ai-ink}"
    rounded: "{rounded.badge}"
  dark-zone-model-aux:
    backgroundColor: "{colors.dark-ai-soft}"
    textColor: "{colors.dark-ink-3}"
    rounded: "{rounded.card}"
  dark-zone-local-aux:
    backgroundColor: "{colors.dark-brand-soft}"
    textColor: "{colors.dark-ink-3}"
    rounded: "{rounded.card}"
  dark-pill-org:
    backgroundColor: "{colors.dark-k-org-bg}"
    textColor: "{colors.dark-k-org}"
  dark-pill-person:
    backgroundColor: "{colors.dark-k-person-bg}"
    textColor: "{colors.dark-k-person}"
  dark-pill-contact:
    backgroundColor: "{colors.dark-k-contact-bg}"
    textColor: "{colors.dark-k-contact}"
  dark-pill-id:
    backgroundColor: "{colors.dark-k-id-bg}"
    textColor: "{colors.dark-k-id}"
  dark-pill-addr:
    backgroundColor: "{colors.dark-k-addr-bg}"
    textColor: "{colors.dark-k-addr}"
  dark-pill-amount:
    backgroundColor: "{colors.dark-k-amount-bg}"
    textColor: "{colors.dark-k-amount}"
  dark-pill-other:
    backgroundColor: "{colors.dark-k-other-bg}"
    textColor: "{colors.dark-k-other}"
---

## Overview

商衡SI 是局域网里的商务文件初审小工具。界面的核心是一条「衡线」：一件工作从本机出发，名称遮住后经网关到模型，再回到本机还原。界面处处画的就是这一趟往返。

青色（衡青）表示本机，紫色（灵紫）只用于和大模型有关的东西。颜色说在哪里，光圈说怎么样。

## Colors

* **衡青 brand**：按钮实色、本机站、本机地带。
* **灵紫 ai**：模型站、模型地带、模型徽标。不用于按钮以外的强调。
* **七类脱敏色**：单位、人名、联系方式、证件、地址、金额、其他。圆点、描边、分段条用原色；胶囊文字用 `-ink` 深字（浅色外壳）。
* **语义色**：红只表示严重或失败，琥珀表示等你、被拦截或中等，天蓝只用于“低”等级与说明。绿色只表示流程完成，不表示文件没问题。

## Typography

SI Sans（Noto Sans SC 子集）与 SI Mono（JetBrains Mono 子集）。数字一律等宽。说明性文字不用 12px。

## Layout

4 为基数。卡片内边距 20～24，卡片之间 16，区块之间 40～56，页面两侧 40（1366 以下 28）。

## Elevation & Depth

画布 → 卡片 → 抬起 → 浮层四层，另有纸面：纸色加纸面阴影，放在凹面舞台上。

## Shapes

控件 10，卡片 14，大卡片 18，浮层 16，首页输入框 24，纸面 6，徽标全圆。

## Components

往返线 SiTrip、地带 SiZone、就地卡 SiNotice、胶囊 SiBadge、行卡 SiRowCard、迷你柱形 SiSpark、环形 SiRing、分段条 SiStack。完整说明见 [澄明二版设计规范（草案）](澄明二版设计规范-草案.md) 第四节。

## Do's and Don'ts

* 进度只用往返线，不另画进度条。
* 模型产出放紫色地带，本机产出放青色地带。
* 不用模糊做动效；只动 transform 与 opacity。
* 十六进制颜色只写在 theme.css，页面与组件只用变量。
