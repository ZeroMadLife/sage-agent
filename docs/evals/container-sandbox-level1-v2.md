# Container Sandbox Level 1 v2

## 1. 解决的问题

旧实现创建容器时已经禁网、限制 CPU/RAM/PID 并将 rootfs 设为只读，但同名 running
容器会被无条件复用。若容器由旧版本创建、配置被手工改动或名称发生冲突，Harness 无法判断
真实隔离配置是否仍然成立。另外，Shell 非零退出会直接抛出 Provider 异常，不能进入统一
Timeline、错误处理和恢复链路。

Level 1 v2 增加：

- `cap-drop ALL`、`no-new-privileges`、默认 seccomp、swap/ulimit、init 和 `rprivate` bind；
- 启动前要求 Rootless Docker 或 Docker Desktop VM，并验证 daemon 启用 seccomp；
- 通过 `docker inspect` 核验 network、rootfs、CPU、内存、PID、tmpfs、ulimit、mount、label；
- 只复用通过完整 profile 的容器；Sage-owned 漂移容器重建，名称冲突 fail closed；
- Shell 非零退出转换为带 `exit_code` 的结构化 `SandboxResult`，继续进入 Harness 治理链。

## 2. 自动化证据

执行命令：

```bash
python scripts/audit_container_sandbox.py \
  --output tmp/sage-container-sandbox-level1-v2.json
```

在 clean source commit `1b7a9f8` 上，Docker Desktop live audit 为 10/10：

| Case | 证据 |
| --- | --- |
| inspect profile | 无配置违规 |
| workspace write | 受控工作区可写 |
| read-only rootfs | `/etc` 写入失败 |
| network disabled | 连接返回 `Network is unreachable` |
| capabilities dropped | `CapEff=0000000000000000` |
| no new privileges | `NoNewPrivs=1` |
| isolated HOME | `/tmp/sage-home` |
| host env isolation | 宿主审计变量不存在 |
| file size limit | 70 MB 文件触发 `File too large` |
| terminal cleanup | close 后容器不存在 |

同一切片还通过 27 项定向 Sandbox 测试和 305 项 Harness 回归。机器可读摘要位于
`evals/reports/container_sandbox_level1_v2_2026-07-25.json`。

## 3. Rootless UID 说明

生产环境的 Docker daemon 由 `sage-sandbox` 非特权宿主用户运行，workspace 通过 ACL 只授予
该用户。容器内 namespace root 映射到这个宿主 UID，不等于宿主 root。任意改成容器 UID
65532 会映射到 subordinate UID，反而无法写入现有 workspace。因此本切片不声明“容器内
non-root”；真正迁移需要先落地只读源 + 独立 writable overlay，再调整 overlay ownership。

## 4. 未关闭边界

- workspace 仍是整体可写 bind mount，尚未变成只读源 + diff overlay；
- 镜像仍使用版本 tag，尚未由部署门禁固定 digest；
- 未引入 gVisor/Firecracker，也没有宣称能抵抗内核级逃逸；
- live audit 在 Docker Desktop 运行，生产 rootless daemon 仍需在部署门禁中复跑；
- 本次验证资源配置和单文件上限，没有在开发机执行 fork bomb 或 OOM 破坏性压力。
