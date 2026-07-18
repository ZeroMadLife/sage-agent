# Sage 私有 Canary 部署 Runbook

> 适用：Ubuntu 22.04 单机、Tailscale 私有 HTTPS、`dev/sage-v7` 已合并版本。
>
> 原则：先 dry-run；不使用 root Docker socket；不在命令行传密码或密钥；不开放公网应用端口。

## 1. 角色与停点

| 步骤 | 执行者 | 是否可交给飞书 Codex |
| --- | --- | --- |
| 安装系统包、创建用户、ACL、systemd、SSH/UFW | root 一次性初始化 | 否 |
| Tailscale 登录、ACL、HTTPS 授权 | 用户 | 否 |
| GitHub OAuth App 和 `/etc/sage/env` 密钥 | 用户 | 否 |
| `deployctl preflight`、dry-run、已合并 SHA 部署 | `sage-deploy` | 是 |
| 认证、数据库、公网入口和防火墙变更 | 用户人工确认 | 否 |

## 2. 一次性主机初始化

以下操作在阿里云控制台终端以 root 执行。先确认当前有可用的 SSH key 会话；完成后旋转已暴露
过的密码，并关闭 SSH 密码登录。

1. 使用 Docker 官方 apt 仓库安装 Docker Engine、Compose plugin 和
   `docker-ce-rootless-extras`；安装 `uidmap dbus-user-session slirp4netns fuse-overlayfs socat acl
   tailscale ufw fail2ban`。不要使用未经审查的 `curl | sh`。
2. 创建两个无 sudo 用户，固定 UID：

   ```bash
   useradd --create-home --uid 1002 --shell /bin/bash sage-deploy
   useradd --create-home --uid 1003 --shell /bin/bash sage-sandbox
   loginctl enable-linger sage-deploy
   loginctl enable-linger sage-sandbox
   ```

3. 确认 `/etc/subuid` 和 `/etc/subgid` 分别为两个用户提供不重叠的至少 `65536` UID/GID 区间。
4. Ubuntu 22.04 的 `user@.service` 默认只委派 `pids memory`。把
   `infra/systemd/sage-rootless-user-delegation.conf` 安装为 sandbox 用户的实例级 drop-in，
   不要改成影响所有登录用户的全局覆盖：

   ```bash
   install -d -m 0755 /etc/systemd/system/user@1003.service.d
   install -m 0644 infra/systemd/sage-rootless-user-delegation.conf \
     /etc/systemd/system/user@1003.service.d/50-sage-rootless-delegation.conf
   systemctl daemon-reload
   ```

5. 分别以两个用户运行 `dockerd-rootless-setuptool.sh install`，并启用 user service。安装 drop-in
   后重启 `sage-sandbox` user manager，再检查控制器与 daemon：

   ```bash
   systemctl restart user@1003.service
   systemctl --user enable --now docker
   cat /sys/fs/cgroup/user.slice/user-1003.slice/user@1003.service/cgroup.controllers
   docker info --format '{{.CgroupDriver}} {{json .SecurityOptions}}'
   ```

   sandbox controller 列表必须包含 `cpu memory pids`；两个 daemon 都必须包含 `rootless`；
   `sage-sandbox` 必须显示 `systemd` cgroup driver。任一条件不满足时部署必须阻断。
   rootless daemon 稳定运行后，可将 `sage-sandbox` 登录 shell 改为 `/usr/sbin/nologin`。

6. 创建应用目录。仓库和密钥只给 `sage-deploy`；workspace 通过 ACL 只额外给
   `sage-sandbox`：

   ```bash
   install -d -o sage-deploy -g sage-deploy -m 0750 /opt/sage/app /opt/sage/config
   install -d -o sage-deploy -g sage-deploy -m 0700 /opt/sage/state /opt/sage/backups
   install -d -o sage-deploy -g sage-deploy -m 0770 \
     /opt/sage/data/workspaces /opt/sage/data/coding /opt/sage/data/knowledge
   setfacl -m u:sage-sandbox:rwx,d:u:sage-sandbox:rwx /opt/sage/data/workspaces
   ```

7. 把 `infra/systemd/sage-sandbox-proxy.service` 安装到 `/etc/systemd/system/`。该固定代理由
   root 启动，但输出 socket 属于 `sage-deploy`、权限 `0600`，只连接独立 sandbox daemon。
   启用后确认：

   ```bash
   systemctl enable --now sage-sandbox-proxy.service
   stat -c '%U %G %a %n' /run/user/1002/sage-sandbox.sock
   stat -c '%U %G %a %n' /run/user/1003/docker.sock
   ```

   第二条由 root 在初始化阶段确认。日常预检只让 `sage-deploy` 检查自己的 daemon 和代理
   socket，再通过代理执行 `docker info`；它不需要、也不应获得 sandbox 私有 socket 的目录权限。

8. `sage-deploy` 的 rootless daemon 运行 Compose；`sage-sandbox` daemon 只运行 Coding
   sandbox。禁止把任一用户加入 `docker` 组，禁止保留 `/var/run/docker.sock` 挂载。
9. 防火墙只保留受控 SSH 和 Tailscale 所需流量；此阶段不开放 80/443/8000/8080/5432/6379。

## 3. 应用与密钥准备

1. 以 `sage-deploy` 把仓库 clone 到 `/opt/sage/app`，checkout 已合入 `dev/sage-v7` 的完整
   commit SHA。部署工作区必须干净。
2. 复制 `infra/env/private-canary.env.example` 到 `/etc/sage/env`，写入真实值后让文件归属
   `sage-deploy:sage-deploy` 并执行 `chmod 600 /etc/sage/env`。用户在编辑器或控制台中输入，
   不让 Codex/Claude 读取原文。
3. Tailscale DNS 名写入 `CLOUD_FRONTEND_URL` 和 GitHub OAuth callback。生产不允许 dev login。
4. 阿里云 ECS 直连 Docker Hub 不稳定时，将 `SAGE_DOCKER_REGISTRY` 保持为
   `docker.m.daocloud.io`，并使用同源 `SAGE_CODING_SANDBOX_IMAGE`；其他网络环境可以显式改回
   `docker.io`。不要在服务器临时改 Dockerfile。
5. 当前首发不启用独立 Worker：保持 `KNOWLEDGE_JOBS_ENABLED=false`。
6. 首次 migration 完成后，由用户本人在服务器终端创建一次性邀请码。不要截屏、写入群聊或让
   Codex/Claude 读取输出；使用后该邀请码自动失效：

   ```bash
   docker compose --env-file /etc/sage/env -f infra/compose/private-canary.yml \
     exec -T api python -m core.cloud.auth.cli create-invite --email <GitHub主邮箱>
   ```

   如果不传 `--email`，任何拿到邀请码且能完成 GitHub OAuth 的人都能首次注册，因此私有
   Canary 默认必须绑定邮箱。

## 4. Tailscale 私有入口

服务器登录 Tailnet 后只代理 loopback 网关：

```bash
tailscale status
tailscale serve --bg http://127.0.0.1:8080
tailscale serve status
```

首次运行可能给出 HTTPS 授权链接，由用户在浏览器确认。不要运行 `tailscale funnel`；Serve
受 Tailnet ACL 约束，Funnel 是公网入口。手机安装 Tailscale、加入同一 Tailnet 后使用
`https://<device>.<tailnet>.ts.net`。

## 5. 预检与部署

所有命令由 `sage-deploy` 执行，并显式连接它自己的 rootless daemon：

```bash
export DOCKER_HOST=unix:///run/user/1002/docker.sock
python scripts/deployctl.py --env-file /etc/sage/env preflight
python scripts/deployctl.py --env-file /etc/sage/env apply --tag <40位commit-sha>
python scripts/deployctl.py --env-file /etc/sage/env --execute apply --tag <40位commit-sha>
```

`preflight` 会通过 0600 代理启动一个无网络、只读、无 capabilities 的一次性 sandbox，实际验证
CPU、内存和 PID 限额。探针使用 `--pull=never`，所以要先把 `SAGE_CODING_SANDBOX_IMAGE` 拉到
独立 sandbox daemon；探针失败时禁止部署。

不加 `--execute` 时只输出计划，不运行构建、迁移或切换。执行路径依次为：预检、构建不可变
SHA 镜像、启动数据服务、PostgreSQL 备份、幂等 migration、API/Web 健康检查、状态落盘。

## 6. 验收

1. `curl http://127.0.0.1:8080/health` 返回 200。
2. `ss -lntp` 只看到 `127.0.0.1:8080`，没有公网 8000/5432/6379。
3. 未登录访问 `/api/v1/coding/models` 与创建 session 返回 401。
4. 未登录访问 `/api/v1/knowledge` 返回 401；遗留 `/api/v1/chat` 在 production 返回 404。
5. 手机关闭 Wi-Fi，经蜂窝网络和 Tailscale 打开 Sage；退出 Tailnet 后 URL 不可访问。
6. 登录后完成一次 Chat、WebSocket、文件读取和受控 Container Sandbox smoke。
7. 在 sandbox daemon 检查容器：`network=none`、rootfs 只读、PID/内存/CPU 限制存在。
8. 日志、飞书摘要和部署状态中没有 cookie、密码、OAuth secret 或 Provider key。

## 7. 回滚与恢复

应用回滚只接受服务器已有的完整旧 SHA，不自动执行数据库 down migration：

```bash
python scripts/deployctl.py --env-file /etc/sage/env rollback --tag <旧40位commit-sha>
python scripts/deployctl.py --env-file /etc/sage/env --execute rollback --tag <旧40位commit-sha>
```

数据库恢复是人工高危操作：先停止 API，验证目标 SQL 备份和当前数据库，再在维护窗口恢复。
不得让 Loop、Codex 或定时器自动恢复数据库。连续三次基础设施失败后停止自动执行并发中文报告。

## 8. 飞书 Codex 与 Claude

- 飞书 Codex：只能在 `codex/*` 分支提交中文 PR；基础设施 PR 合入后，允许调用固定
  `deployctl preflight/apply/rollback`。`apply/rollback` 默认 dry-run，执行必须带明确 SHA 和
  `--execute`。
- Claude：只读检查 PR diff、CI、Compose config、镜像 smoke 和脱敏健康摘要；不拿 SSH、
  `/etc/sage/env` 或部署 socket。
- 域名和 ICP 完成后另开公网切换 PR；在此之前不开放 80/443，也不使用 Funnel。
