# Sage Desktop Sidecar D0

本目录的 D0 入口只证明 macOS arm64 的 Python sidecar 可打包和独立启动，不包含 Keychain、OAuth、DMG 或更新器。D1 安全宿主复用同一产物，并通过隐藏的 `--desktop-host` pipe 合同启用 bearer/Host/Origin 门禁。

`requirements-runtime.txt` 与 `requirements-build.txt` 描述最小直接依赖，`requirements-lock.txt` 冻结完整构建环境。调用环境只需提供 Python 3.12：

```bash
python -m desktop.sidecar.build --output-dir build/desktop-sidecar
```

构建器自行创建隔离 venv，按 lock 安装固定依赖，使用固定 Hatchling 将本地 Harness 构建为 wheel，并校验实际 distribution manifest、direct URL 元数据与 wheel/source hash。随后生成 PyInstaller one-dir 产物 `sage-api-aarch64-apple-darwin`，在不继承 `PYTHONPATH`、调用方虚拟环境和 Node 路径的临时目录中验证随机 loopback、健康合同、SQLite/checkpoint 重开、TLS client、核心 import、完整进程组清理和优雅退出。依赖证明与验收结果写入产物旁的 `build-receipt.json`。

源码非宿主进程合同：

```bash
python -m desktop.sidecar --bind 127.0.0.1 --port 0 --data-dir /tmp/sage-data
```

首行标准输出是动态端口启动 receipt。

D1 macOS app 只能从 clean HEAD 使用仓库正式入口构建：

```bash
python3.12 -m desktop.bundle --output-dir /private/tmp/sage-desktop-<sha>
```

入口会重新构建并验证 D0 receipt，原子 staging 到 Tauri resources，显式注入 `SAGE_BUILD_SHA`，生成和 ad-hoc 签名 `.app`，再执行真实启动、sidecar crash/restart、显式退出和零残留 smoke。禁止手工复用 `frontend/src-tauri/binaries/sidecar` 中被忽略的旧产物。
