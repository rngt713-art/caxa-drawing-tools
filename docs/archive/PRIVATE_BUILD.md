# 私有构建仓库说明

本目录用于家庭设备或其他内部构建机复现 `图纸上传助手.exe` 和
`caxa_helper.exe`。公开仓库不包含 CAXA 2011r3 引用 DLL。

## 目录

- `vendor/caxa-2011r3/`：编译 helper 所需的 CAXA/.NET 引用 DLL。
- `vendor/caxa-2011r3/SHA256SUMS.txt`：DLL 哈希清单。
- `release/`：当前本地发布 EXE 和发布说明。
- `app/assets/`：窗口和 EXE 图标资源。

## 编译 helper

在仓库根目录执行：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\app\caxa\caxa_helper\build_helper.ps1 -Force
```

脚本优先使用 `vendor/caxa-2011r3/` 中完整的 CAXA 引用，缺少 CAXA 引用时
使用本机完整的 CAXA 安装引用。私有目录缺少的 Microsoft 框架引用可由本机 .NET
提供。成功后打印源码和 EXE 的 SHA-256，并将两者记录在本机的
`build/caxa_helper.json`。

## 编译上传端

先按上一步编译 helper，再执行：

```powershell
python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
pwsh -NoProfile -ExecutionPolicy Bypass -File .\build_all.ps1 -Uploader -Force
```

脚本按本机编译记录核对 helper 源码和 EXE，再把
`app/caxa/caxa_helper/caxa_helper.exe` 嵌入 PyInstaller 产物。编译记录缺失或哈希
不符时，运行 `build_all.ps1 -Helper -Force` 重建。上传端使用
`app/assets/app-icon.ico` 作为 EXE 图标，并打包 ICO 与 PNG 窗口图标资源。

## 发布前检查

1. 运行 `python -m unittest discover -s tests -v`。
2. 运行 `python -m compileall -q app`。
3. 核对 helper 与 uploader 的 SHA-256。
4. 在真实 CAXA 客户端做一次只读预检或小批验收。

签名私钥、PFX 密码和 CAXA 账号密码不得提交到本仓库。
