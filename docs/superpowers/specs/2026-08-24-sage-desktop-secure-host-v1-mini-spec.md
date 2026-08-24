# Sage D1 安全桌面宿主 mini-spec

## 1. 范围与事实边界

D1 在 D0 `desktop-minimal` sidecar 之上交付 macOS arm64 本地开发壳。它只证明 Tauri 2、Vue production build、安全 loopback、sidecar 生命周期和能力诊断；完整学习工作台 API、Keychain、Provider onboarding、Cloud OAuth、updater、Developer ID 签名、公证和 DMG 属于后续切片。缺少签名凭据时，产物只能称为 ad-hoc/local dev package。

## 2. 公共 seam

### 2.1 bootstrap 与 handshake

Tauri 只以固定参数 `--desktop-host` 启动固定 `externalBin`。Rust 通过 child stdin 匿名 pipe 写入一行 JSON，字段为：

D0 采用 PyInstaller one-dir，不能只把 Python 主可执行文件交给 `externalBin`。D1 选定的 macOS bundle 合同是：`externalBin` 为极小、随 App 签名的 arm64 Mach-O launcher；完整且只读的 one-dir 位于 `Contents/Resources/sidecar`。launcher 只接受固定 `--desktop-host` 参数，并从自身 bundle 位置计算固定相对路径后用 `execv` 替换自身为真正 sidecar。`exec` 不改变 PID，所以 Rust 的 child PID 校验仍然成立。禁止把 `_internal` 复制到用户可写目录，禁止从前端、环境变量或任意 argv 接受 sidecar 路径。

合同依据是 Tauri 2 官方的 [Embedding External Binaries](https://v2.tauri.app/develop/sidecar/) 与 [Embedding Additional Files](https://v2.tauri.app/develop/resources/)：`externalBin` 要求带 target triple 的单个可执行文件，`bundle.resources` 会保留目录结构并落入 `$RESOURCE`。官方文档没有给出把 PyInstaller one-dir 整体直接声明为 `externalBin` 的模式，因此 D1 不声称 launcher 是官方 one-dir 范式；它只是同时遵守两个官方打包合同，并通过真实 `.app` 布局、PID 不变和签名验证补足组合边界。

```json
{
  "instance_id": "uuid",
  "nonce": "base64url-random",
  "bearer": "base64url-random",
  "origin": "tauri://localhost",
  "data_dir": "/app-owned/path"
}
```

sidecar 绑定 `127.0.0.1:0` 后，通过 child stdout 匿名 pipe 返回且只返回一行握手：

```json
{
  "pid": 123,
  "port": 49152,
  "instance_id": "uuid",
  "api_version": "1",
  "build_sha": "immutable-source-sha",
  "nonce": "base64url-random"
}
```

Rust 必须同时校验 child PID、动态非零端口、instance、nonce、API version 和 build SHA。任一字段错误、超时、重复行或子进程提前退出均 fail closed，Vue 在校验完成前拿不到 endpoint。D0 的非宿主启动合同继续可用。

`origin` 由 Rust 运行 profile 决定：production 精确为 `tauri://localhost`，dev 精确为 `http://127.0.0.1:5173`。sidecar 只接受这两个值，当前进程只绑定其中一个；不得接受尾斜杠、`localhost` 别名、通配符或调用方自定义 Origin。

### 2.2 DesktopHostAdapter

Vue 只通过 Tauri invoke 获得内存态 `{endpoint, bearer, instanceId, state}`。Adapter 不写 `localStorage`、URL、日志或持久 store；HTTP 和 fetch-based SSE 设置 `Authorization`，WebSocket 通过 `Sec-WebSocket-Protocol` 的 `sage-bearer.<token>` 项传递 bearer，禁止 query token。

### 2.3 loopback 门禁

安全 profile 的每个 HTTP、SSE 和 WebSocket 请求必须同时满足：

- bearer 与当前进程内常量时间比较一致；
- `Host` 精确等于 `127.0.0.1:<bound-port>`；
- `Origin` 精确等于 bootstrap 的桌面 Origin；
- 生产不安装通用 CORS middleware，也不接受 `*`。

WebView 的带 `Authorization` 跨 Origin 请求允许一次最小预检：OPTIONS 不要求 bearer，但 Host、Origin、requested method 和 requested headers 必须精确匹配；只返回当前 Origin、`GET`、`Authorization` 和 `Vary: Origin`，不返回 wildcard 或 credentials。实际响应继续校验 bearer/Host/Origin，并只向可信 Origin 返回精确 ACAO。失败只返回稳定 `reason_code`，不回显 bearer、nonce、路径或用户内容。

debug WebView navigation 只允许 `http://127.0.0.1:5173` 同源页面，拒绝 `localhost`、其他端口和 HTTPS；release 只允许 `tauri://localhost` 自有 Origin。SSE 由 `DesktopHostAdapter` 持有可取消 body reader；headers 成功后的 read error 或正常 EOF 都必须进入 degraded，仅刷新一次宿主 session 并用轮换后的 endpoint/bearer 有界重连，第二次 EOF/断流 fail closed，不保留旧 ready/session。消费者 cancel 会使当前 recovery generation 失效、abort 新 fetch 并取消当前或迟到 body，之后不得再创建 reader 或发布 ready。调用方 `AbortSignal` 属于单流主动取消：initially-aborted、初始 401/403 recovery pending 或 active read abort 都不得触发 degraded、安装或清空共享 session、第二次 fetch；迟到 resolve/reject 必须转换为 caller abort 并保持原 session。共享 session 的安装和清理由 identity revision/CAS 保护，revision 只在 `{endpoint,bearer,instanceId}` 语义值或有无 session 实际改变时推进；同值状态轮询不得使 SSE/HTTP recovery 丧失所有权。并发 `desktopHostStatus` 另用请求 generation 保证后发请求和 `desktopExit` 胜出，迟到旧 snapshot 不得覆盖新 session 或在退出后重装。旧 SSE 的 recovery 在取消后迟到 resolve、reject 或 cancel 时，均不得覆盖或清除其他并发请求已经安装的新 session。

### 2.4 健康与能力

- `/health/live`：进程活着；
- `/health/ready`：D0 七项最小检查继续有效；
- `/capabilities`：返回总状态和 `api/storage/checkpoint/provider/knowledge/sandbox` 各项的 `ready/degraded/blocked + reason_code + action`。

顶层状态页直接使用 capabilities 总状态的 `ready/degraded/blocked`。HTTP/SSE 的网络或 401/403 失败只允许重取一次 host session；WebSocket 异常关闭最多退避重连三次。瞬时失败进入 degraded 并继续轮询，不能永久持有旧 endpoint/bearer。浏览器可见错误只使用 allowlist 的 `reason_code/action`；本地 JSONL 诊断只写 timestamp/event/state/reason_code，固定命令打开 app-owned 诊断目录。

## 3. 生命周期状态机

| 事件 | sidecar 行为 | UI 行为 |
| --- | --- | --- |
| 窗口关闭/隐藏 | 保持运行 | 重开只重连 |
| HTTP/SSE/WS 断开 | 保持运行 | 重取宿主状态并重连 |
| sidecar crash | 记录 crash，退避后重启 | 显示 degraded/interrupted |
| 10 分钟第 4 次 crash | 不再重启 | blocked，进入诊断页 |
| 显式退出 | 停止接收新连接，SIGTERM grace 后强杀 | 退出应用 |
| 机器重启 | 清理经身份校验的已知 orphan | 重新握手，不自动恢复副作用 |

crash 时间戳和已知 sidecar 的 pid/start-time/executable identity 可以写入 Desktop Host 状态；bearer、nonce、endpoint 和用户正文不得写入。PID 重用或 executable identity 不匹配时禁止清理。orphan 清理和显式退出在 TERM 前、grace 内、KILL 前和 KILL 后都重新观察 pid/start-time/executable；任何身份变化立即停止信号。状态使用 `0600` 临时文件、fsync 和 rename 原子落盘，写入或 rename 失败不得发布 ready，crash budget 持久化失败进入明确 blocked。

## 4. Tauri 最小权限

- capability 不展开 `core:default` 或任何 core/plugin 权限；只注册固定 custom commands `desktop_host_status`、`desktop_exit` 和 `desktop_open_diagnostics`；
- `externalBin` 路径固定，参数固定，不开放 shell execute/spawn command；
- CSP 默认 `default-src 'self'`，connect 仅允许 loopback；
- 禁止任意远程导航、新窗口和生产 devtools；
- D1 不开放文件系统 API，因而不存在跨 workspace 文件访问能力；
- single-instance plugin 必须在其他 plugin 之前初始化。
- plugin 的真实 second-instance callback 必须调用与测试相同的 `single_instance_action`/effect seam，固定执行 show 后 focus。

## 5. 验收证据

自动测试覆盖错误 nonce/PID/build/API version/端口、错误 bearer/Host/Origin、HTTP/SSE/WS 一致门禁、第二实例回调、已知 orphan 身份、窗口隐藏/连接断开/crash/显式退出、10 分钟 crash budget。最终在 clean commit 上构建 D0 one-dir sidecar，作为 Tauri external binary 构建 macOS arm64 local dev app，并以临时应用数据目录完成真实 handshake、live/ready/capabilities 和进程清理 smoke。

唯一正式入口为 `python3.12 -m desktop.bundle --output-dir <仓库外新目录>`。入口要求 clean HEAD，从隔离 D0 构建开始，验证 source SHA/dirty/target/依赖 manifest/Harness hash/逐文件 hash/全部 smoke，原子 staging 新 sidecar，显式注入 `SAGE_BUILD_SHA`，构建 `.app` 后核对 bundle 内 receipt、ad-hoc 签名并执行真实 launch/crash/restart/WebView 新会话认证/explicit-exit/零残留 smoke。WebView 证据是每个 sidecar 进程只写一次的脱敏 capabilities 访问记录，只含 timestamp/event/state/reason_code。macOS CI 使用同一入口；Developer ID、公证、DMG 仍不在 D1。

CI 固定 GitHub 官方 arm64 `macos-15` runner和 Rust `1.98.0`，本地前置条件见 `desktop/sidecar/README.md`。终止 sidecar 时，只有已验证的目标进程退出后才能清除 orphan；信号失败或 SIGKILL 后同一身份仍存活时必须保留记录、持久化 blocked 状态并写脱敏诊断。`supervisor.rs` 的 800+ 行多职责仍是已登记技术债，D1 不为拆分牺牲现有真机证据；进入 D2 前优先无行为变化地提取 diagnostics/state repository，再承载 Keychain/Provider 生命周期。
