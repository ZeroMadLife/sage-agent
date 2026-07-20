# Sage Public Release Pipeline 实施计划

> 日期：2026-07-20
> 基线：`dev/sage-v7@a60fcc4`
> 范围：公开静态门面的 CI、受限 ECS 发布、健康检查与回滚

## 1. 问题

公开门面已经通过 ECS 的 `80` 端口可访问，但当前容器来自一次人工镜像导入和手工切换。
`dev/sage-v7` 后续更新只会进入 rootless Canary，公网入口不会自动继承；人工操作也缺少
不可变版本、候选健康检查、部署状态和一致的失败回滚。

## 2. 决策

沿用本机 `canaryctl` 作为 CI 判定和定时调度入口，不向 GitHub Actions 写入 ECS SSH 或
root 凭据。服务器新增 root-owned `public_releasectl`，只从标准输入接收严格 JSON：

```json
{"action":"apply","tag":"<40 位小写 commit SHA>"}
```

`sage-deploy` 只能通过固定 sudoers 命令无参数调用该控制器。控制器不能读取
`/etc/sage/env`，不能接收镜像名、容器名、端口、路径或 shell 文本。

## 3. 发布流

```text
dev/sage-v7 新 SHA
  -> python / backend-quality / frontend-quality / public-release 全绿
  -> 服务器 checkout 精确 SHA
  -> rootless deployctl 构建 sage-public:<SHA>
  -> public_releasectl 校验 OCI revision 与 65532:65532 用户
  -> 导入 root Docker
  -> 127.0.0.1:18081 候选容器 smoke
  -> 当前 live 停止并改名为 previous
  -> 新容器绑定 80，执行宿主机健康检查
  -> 本机从公网 IP 再做外部 smoke
  -> 写入 current / previous / deployed_at
```

任一候选或切换健康检查失败时不写状态；切换已经开始则恢复 previous。外部 smoke 失败时，
`canaryctl` 请求显式回滚到控制器返回的上一完整 SHA。

## 4. CI 门禁

`frontend-quality` 同时执行主应用和 `build:public`。独立 `public-release` job 构建
`sage-public:${GITHUB_SHA}`，核验：

- OCI revision 等于精确 commit SHA；
- 默认用户为 `65532:65532`；
- rootfs 只读、drop `ALL` capabilities、`no-new-privileges`；
- 独立 bridge，不加入 private Canary 的 Compose 网络；浏览器侧继续由 CSP `connect-src none`
  禁止发起网络请求。容器级出站防火墙延后为独立主机安全切片，不能用会破坏端口发布的
  `--internal` 网络冒充已完成隔离；
- 静态首页包含预期标题且可从回环端口访问。

## 5. 一次性服务器安装

代码合入并同步到 `/opt/sage/app` 后，以 root 执行：

```bash
sh /opt/sage/app/infra/install/install-public-release-controller.sh /opt/sage/app
```

安装器会校验 Python、sudoers，并把控制器复制为 root-owned
`/usr/local/sbin/sage-public-releasectl`。public 发布状态单独保存在 root-only 的
`/var/lib/sage-public-release/state.json`，不会修改 private `deployctl` 使用的
`/opt/sage/state` 权限。日常状态查询不需要 root shell：

```bash
printf '%s\n' '{"action":"status"}' \
  | sudo -n /usr/local/sbin/sage-public-releasectl
```

显式回滚只接受已经存在的完整 SHA：

```bash
printf '%s\n' '{"action":"rollback","tag":"<previous SHA>"}' \
  | sudo -n /usr/local/sbin/sage-public-releasectl
```

## 6. 非目标与后续

- 不开放私人 Harness、API、PostgreSQL 或 Redis。
- 不把 root 密钥或服务器环境文件交给 GitHub Actions。
- 不在本切片实现域名、TLS 或 ICP；原始 IP 的 HTTP 仍会产生 COOP 不可信来源警告。
- 不在本切片实现 public-only Agent API；公开问答仍是独立静态、确定性体验。
- 域名可用后，将外部 smoke 地址替换为 HTTPS 域名，并由主机网关负责证书和 80/443。
