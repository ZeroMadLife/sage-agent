---
title: 公开站为什么不提供写笔记入口
date: 2026-07-20
summary: 所有人都能访问的门面只负责展示与限定问答；写作必须留在私有工作台。
tags: [public, security, publishing]
related:
  - label: 私有发布工作室
    href: https://github.com/ZeroMadLife/sage-agent/tree/dev/sage-v7/frontend/src/views/PublishingStudioView.vue
---

## 结论

公开站是**只读工程现场**，不是开放编辑的博客后台。

如果在 `http://公网地址/` 放一个“写笔记”按钮，就等于默认所有访客都能摸到作者编辑入口。即便按钮后面再套登录，也会把“写作能力”暴露到错误的信任边界上。

## 正确边界

| 表面 | 谁能访问 | 能做什么 |
| --- | --- | --- |
| 公开站 `/` `/notes` | 匿名访客 | 阅读项目、证据、已发布笔记；静态公开问答 |
| 私有应用 `/#/publishing` | 本机 / 鉴权后的作者 | 写 Markdown 草稿、本地预览 |
| 后续发布 API | 作者 + 审核门禁 | 把已验证内容导出为公开 notes |

## 当前实现

1. 公开笔记来自仓库内静态 Markdown：`frontend/public-content/notes/`
2. 作者写作仍在主应用 **发布工作室**，草稿只保存在本机
3. 发布按钮仍禁用，等待后端发布契约
4. Ask Sage 只读公开语料，不写文件、不碰私人工作区

## 以后怎么接“我能写笔记”

不是在公开站加编辑器，而是：

1. 私有工作室写完并审核
2. 生成公开 notes 包
3. 重新构建 / 发布 `sage-public` 静态产物

这样访客永远只看到结果，看不到写入口。
