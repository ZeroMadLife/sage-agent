# Sage Desktop Sidecar D0

本目录只证明 macOS arm64 的 Python sidecar 可打包和独立启动，不包含 Tauri、Keychain、OAuth、DMG 或更新器。

`requirements-runtime.txt` 与 `requirements-build.txt` 描述最小直接依赖，`requirements-lock.txt` 冻结完整构建环境。使用全新的 Python 3.12 环境运行：

```bash
python -m pip install -r desktop/sidecar/requirements-lock.txt
python -m desktop.sidecar.build --output-dir build/desktop-sidecar
```

命令生成 PyInstaller one-dir 产物 `sage-api-aarch64-apple-darwin`，随后在不继承 `PYTHONPATH`、虚拟环境和 Node 路径的临时目录中验证随机 loopback、健康合同、SQLite/checkpoint 重开、TLS client、核心 import 和优雅退出。验收结果写入产物旁的 `build-receipt.json`。

源码进程合同：

```bash
python -m desktop.sidecar --bind 127.0.0.1 --port 0 --data-dir /tmp/sage-data
```

首行标准输出是动态端口启动 receipt。D0 不实现安全 bearer/nonce/pipe 握手；这些属于 D1。
