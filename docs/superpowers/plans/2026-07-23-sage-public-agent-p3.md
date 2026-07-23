# Sage Public Agent P3：PublishedPackage 生命周期

## 目标

把 P2 的“镜像内固定 JSON 资料包”升级为可审计的公开知识包发布闭环：候选包先校验，再通过原子 active pointer 激活；撤回当前 revision 时自动回退到上一条已验证 revision。公开 Agent 只读取 active package，不获得私人 Harness、Knowledge、Memory、MCP 或工作区权限。

## 交付边界

- `public_agent/registry.py` 提供 root-owned `registry.json` 与不可变 `packages/<package_id>/<revision>.json`。
- package 状态为 `candidate | active | inactive | revoked`；同一 `(package_id, revision)` 不允许内容覆盖。
- `publish` 和 `revoke` 使用 `expected_active_revision` 做 compare-and-swap，避免两个部署任务互相覆盖。
- registry 状态通过临时文件、`fsync`、`os.replace` 原子更新；事件保留在同一 registry 文件，包含操作人、时间、digest、撤回原因和回退 revision。
- 公开 Agent 每次请求冻结一个 active package，问答 receipt 与该快照 revision/digest 一致；发布或撤回不需要重启进程。
- public release controller 只读挂载 `/var/lib/sage-public-release/packages` 到 Agent，启动时校验 root-owned、无符号链接、不可被组/其他用户写入。
- root-owned `sage-public-packagectl` 只接受受限 JSON stdin；资料包必须在公开 disclosure、HTTPS、文档 digest 和大小约束下通过现有 `PublicPackage` 校验。

## 生命周期

```text
private approved candidate
        │ 受控部署输入
        ▼
stage ──> candidate ── publish(CAS) ──> active
                                      │
                                      ├── publish(next) ──> inactive
                                      └── revoke ──> revoked + previous inactive -> active
```

没有上一条健康 `inactive` revision 时，撤回当前 active 会被拒绝，服务继续保持当前版本。旧 package 文件和审计事件不会被删除，便于回溯与恢复。

## 控制面命令

`/usr/local/sbin/sage-public-packagectl` 仅允许 `sage-deploy` 通过精确 sudoers 命令调用，输入示例：

```json
{"action":"status"}
```

```json
{"action":"publish","package_id":"sage-public","revision":"2026-07-23.1","expected_active_revision":"2026-07-22.1"}
```

```json
{"action":"revoke","package_id":"sage-public","revision":"2026-07-23.1","expected_active_revision":"2026-07-23.1","reason":"资料需要重新审核"}
```

`stage` 只接受完整公开 JSON object，不接受任意服务器路径；操作失败不会修改 active pointer。

## 验证证据

- `tests/public_agent/test_public_package_registry.py`：不可变文件、CAS、发布、回退、无回退拒绝、损坏包 fail-closed。
- `tests/public_agent/test_api.py`：同一进程中发布/撤回后 receipt 与响应 header 立即跟随 active revision。
- `tests/scripts/test_public_packagectl.py`：stdin action 严格白名单，禁止路径型操作。
- `tests/scripts/test_public_releasectl.py`：Agent 只读挂载 registry，保留 P2 隔离与发布探活。
- `tests/contracts/test_private_canary_deployment.py`：安装脚本、sudoers 和公共运行边界。

## 不在本阶段

- 不把私人 Knowledge Proposal 自动变成公开 package；仍需独立审核/导出步骤。
- 不增加公网 package mutation API；控制面只走受限服务器命令。
- 不修改主对话、Knowledge 图谱或博客前端重构；前端只需继续消费已有 public Agent ask/receipt。
- 不接入 Web Search、MCP 或长期 Memory。

## 下一阶段

部署并初始化 registry 后，用博客公开页面完成真实 revision 切换、撤回和回退联调；确认稳定后再把“已批准 public_candidate -> stage”接到发布流水线，并补 P3 的发布审核 UI 契约。

## P3.1：公开候选审批桥

P3.1 将“公开内容审核”和“服务器发布权限”明确拆开：

1. 私有 API 接收完整 `sage-public` package，先复用 Public Agent 的 disclosure、HTTPS、document digest 和 2 MiB 大小门禁。
2. package 正文创建后不可修改；同一公开 revision 不能用不同 digest 再次提交。
3. 候选按登录用户隔离，列表不返回正文；详情、批准、拒绝和导出均使用 `Cache-Control: no-store`。
4. 批准/拒绝带 `expected_revision`，决策写入 append-only event；未批准候选不能导出。
5. 已批准候选只导出确定性的 `{"action":"stage","package":...}` artifact，不直接调用 root controller。部署流水线再把 `stage_request` 交给 `sage-public-packagectl`，由宿主机 registry 记录真正的 staged receipt。

接口：

```text
POST /api/v1/publication/candidates
GET  /api/v1/publication/candidates
GET  /api/v1/publication/candidates/{candidate_id}
POST /api/v1/publication/candidates/{candidate_id}/approve
POST /api/v1/publication/candidates/{candidate_id}/reject
GET  /api/v1/publication/candidates/{candidate_id}/stage-artifact
```

本阶段不让私人 API 持有 `sudo` 或 `/var/lib/sage-public-release` 写权限，也不把私有 Knowledge Source Proposal 自动升级为公开披露授权。Publishing Studio 后续只需接候选创建与审批视图；服务器 stage/publish/rollback 继续沿用 P3 已验证的控制器。
