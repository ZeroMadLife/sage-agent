# Sage RAG 多模态证据链 v1

> 日期：2026-07-28
>
> clean source：`cc8744377c3bd526b2ba6a59c56f5df2e5d252b3`
>
> 报告：[knowledge_multimodal_evidence_v1_2026-07-28.json](../../evals/reports/knowledge_multimodal_evidence_v1_2026-07-28.json)
>
> report sha256：`927ed23eb6b5133f2fdb3e898202480368e04c866a6a4e8d7ae76dedf1a09860`
>
> deterministic digest：`sha256:87e492d57564cccb4131dafc034f93ecb5bc45949b1cb5deeb05e81263908fda`

## 这次解决什么

PR-7 只解决“解析出的视觉证据能否被稳定引用”，不把所有非文本文件直接归为一个
“多模态 RAG”功能：

```text
DOCX / PNG / PDF
  -> L1 本地结构解析，或显式启用的 L2 VLM 区域抽取
  -> ParsedBlock(page, normalized bbox, media_ref, confidence, parser)
  -> KnowledgeChunk
  -> SQLite / PostgreSQL derived index
  -> HTTP citation / Coding tool / Harness metadata
  -> Knowledge Inspector
```

这里的核心不变量是 citation 能回到同一 source revision、page 和归一化视觉区域。召回仍使用
文本 sparse/dense/hybrid 路线；本 PR 没有引入页面图片 embedding、ColPali/ColQwen 或 ANN。

## L1：本地确定性解析

### DOCX

- 使用 `python-docx==1.2.0` 的 `Document.iter_inner_content()` 按顶层顺序读取 paragraph/table。
- 保留 heading path、列表和 Markdown table；从 OOXML ZIP 的 `word/media/` 枚举嵌入图片。
- DOCX 本身没有稳定分页信息，本阶段不渲染 Word，因此 DOCX block 的 `page/bbox` 保持空值，
  不伪造页码或图片坐标。

### PNG

- Pillow 先 `verify()`，再重新打开并 `load()`，限制为 4000 万像素。
- 只索引可信的 Title/Description 元数据和图片尺寸；没有描述时明确记录需要 OCR/VLM，
  不从像素臆造文字。
- 原图作为 `page=1`、`bbox=[0,0,1,1]` 的归一化整图区域，可形成稳定 citation。

`python-docx` 官方说明 `iter_inner_content()` 按文档顺序返回顶层 paragraph/table，同时其公开
shape API 只覆盖 inline picture；Pillow 官方也要求 `verify()` 后重新打开图片再读取像素。参考：
[python-docx Document API](https://python-docx.readthedocs.io/en/latest/api/document.html)、
[python-docx inline shapes](https://python-docx.readthedocs.io/en/latest/user/shapes.html)、
[Pillow Image API](https://pillow.readthedocs.io/en/stable/reference/Image.html)。

## L2：受控 VLM 区域证据

现有外部解析策略继续默认关闭并受 source-root allowlist 约束。`qwen3-vl@2.0.0` 新增 PNG 与
严格 JSON region 合同：每个 region 必须包含 `kind/text/bbox/confidence`，bbox 为 0 到 1 的
有限数值且面积为正；非法类型、布尔值、越界、空区域或混合 structured/legacy page 均 fail
closed。旧 Markdown 响应仍兼容，但不会被补造 bbox。

这条路径使用通用 Qwen VL 结构化响应，不能称为专用 OCR。Qwen 官方文档将带 bbox 的高级
识别与通用视觉理解区分，并建议 OCR 场景使用专用 Qwen OCR：
[Qwen OCR API](https://www.alibabacloud.com/help/en/model-studio/qwen-vl-ocr-api-reference)、
[Qwen Vision](https://www.alibabacloud.com/help/en/model-studio/vision-model)。

## 持久化与兼容

SQLite 和 PostgreSQL chunk 投影都新增以下字段：

```text
block_kind
bbox / bbox_json
media_ref
confidence
parser_id
parser_version
```

两套 `ensure_schema` 都通过 additive migration 兼容已有表。纯文本 chunk ID 算法保持不变；
只有存在 `bbox` 或 `media_ref` 的视觉证据才把视觉元数据加入 chunk identity，所以区域变化会
产生新 citation，而无关文本语料不会整体失效。API 额外显式返回
`bbox_coordinate_space=normalized`，防止调用方把归一化坐标误当像素。

## 评测合同

PR-1 的 80 条正式检索集没有 image case，不能为了 PR-7 临时修改冻结 test。因而本阶段新增
独立 `multimodal_cases.jsonl`：

| 项目 | 数量/范围 |
| --- | --- |
| case | 12 条项目自建 synthetic fixture |
| split | `dev/calibration/test=6/3/3` |
| L1 | DOCX paragraph/table/media；PNG metadata/无描述/整图 bbox |
| L2 | Qwen VLM structured region fixture response |
| 门禁 | case pass、冻结 test、bbox accuracy、citation identity stability 均为 100% |

正式报告在 clean implementation commit 上生成：12/12 case 通过，冻结 test 3/3 通过，7 个
带 gold bbox 的 case 区域准确率 100%，重复 block 的 citation identity 保持稳定。单机合成
fixture 的解析+投影 P50/P95 为 `0.036/27.860 ms`；这不是检索、真实 VLM 或生产 SLA。

fixture 输入与响应都由项目自建且不联网，因此这些数字能证明工程合同可复现，不能证明真实
扫描件、复杂表格或视觉模型质量。报告显式记录 `live_vlm_quality_evaluated=false` 与
`visual_vector_retrieval_enabled=false`。

## Trade-off 与面试边界

1. **为什么先 L1/L2，不直接做视觉检索？** 当前缺口是证据定位和持久化。没有 image query
   benchmark 前引入 ColPali/ColQwen，只会增加模型、显存、索引体积和在线延迟，无法证明收益。
2. **为什么 bbox 统一为 0 到 1？** 避免依赖原图分辨率；前端可按实际渲染尺寸换算，同时
   citation identity 能跨展示尺寸稳定。
3. **为什么 DOCX 图片没有 bbox？** `python-docx` 读取 OOXML 结构，不是 Word 排版引擎。
   页码和坐标依赖字体、页面设置与渲染环境，留空比伪造更可审计。
4. **为什么 PNG 元数据不是 OCR？** 它没有识别像素文字。只有显式启用的 VLM/OCR Provider
   才能产生区域文本，并必须记录 parser/version/confidence。
5. **这次能写进简历什么？** 可以写“建立 DOCX/PNG 与 VLM 视觉区域的版本化证据链，贯通
   Parse Artifact、SQLite/PostgreSQL、API/Harness citation，并用 12 条 fixture contract
   case 验证 100% 字段与区域不变量”。不能写“真实 OCR 准确率 100%”或“已上线视觉检索”。

## 尚未完成

- 没有可用 Qwen 凭据，未运行真实 VLM 质量/成本/延迟评测。
- 没有真实扫描 PDF、截图问答与复杂表格的人工标注 image/mixed benchmark。
- 没有 DOCX 渲染层，因此没有可靠 page/bbox。
- 没有视觉向量召回、跨模态 query embedding 或 ColPali/ColQwen。
- 不部署、不接飞书、不合入 `main`。
