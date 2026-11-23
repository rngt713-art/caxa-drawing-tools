# 编译 caxa_helper.exe（仅能在安装了 CAXA 2011 客户端的工作设备上运行）
#
#   powershell -ExecutionPolicy Bypass -File .\build_helper.ps1            # 编译
#   powershell -ExecutionPolicy Bypass -File .\build_helper.ps1 -PlanOnly  # 只检查依赖路径
#   powershell -ExecutionPolicy Bypass -File .\build_helper.ps1 -Force     # 允许覆盖已有 exe
#
# 重要：源码与 exe 必须一一对应。仓库不再附带旧 exe；编译成功后会打印 exe 的
# SHA-256，便于和发布记录核对。找不到 CAXA DLL 时脚本会明确失败，不会伪造成功。

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$cs = Join-Path $PSScriptRoot "caxa_helper.cs"
$out = Join-Path $PSScriptRoot "caxa_helper.exe"
$buildRecord = Join-Path $PSScriptRoot "..\..\..\build\caxa_helper.json"
$csc = "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"

$caxaRefNames = @(
    "CAXA.A5.EAP.Service.dll",
    "CAXA.A5.EAP.CommonLibrary.dll",
    "Interop.EAPLib.dll",
    "CAXA.A5.Collection.dll",
    "CAXA.A5.EAP.Common.dll",
    "CAXA.A5.CAD.Common.dll",
    "CAXA.A5.EventSupport.dll"
)
$frameworkRefNames = @(
    "System.Web.Extensions.dll",
    "UIAutomationClient.dll",
    "UIAutomationTypes.dll"
)

# Use the complete private CAXA reference set or the complete installed set.
# Microsoft framework references can come from the local .NET installation.
$vendorDir = Join-Path $PSScriptRoot "..\..\..\vendor\caxa-2011r3"
$vendorCaxaRefs = @($caxaRefNames | ForEach-Object { Join-Path $vendorDir $_ })
$installedCaxaRefs = @(
    "C:\Program Files (x86)\Common Files\CAXA Shared\CAXA Integration\EAPInterface\1.0\EAP 2011r3\CAXA.A5.EAP.Service.dll",
    "C:\Program Files (x86)\Common Files\CAXA Shared\CAXA Integration\EAPInterface\1.0\EAP 2011r3\CAXA.A5.EAP.CommonLibrary.dll",
    "C:\Program Files (x86)\Common Files\CAXA Shared\CAXA Integration\EAPInterface\1.0\EAP 2011r3\Interop.EAPLib.dll",
    "C:\Windows\Microsoft.NET\assembly\GAC_32\CAXA.A5.Collection\v4.0_1.0.0.0__207da9a5ab81a7c8\CAXA.A5.Collection.dll",
    "C:\Windows\Microsoft.NET\assembly\GAC_32\CAXA.A5.EAP.Common\v4.0_1.0.0.0__207da9a5ab81a7c8\CAXA.A5.EAP.Common.dll",
    "C:\Windows\Microsoft.NET\assembly\GAC_32\CAXA.A5.CAD.Common\v4.0_1.0.0.0__207da9a5ab81a7c8\CAXA.A5.CAD.Common.dll",
    "C:\Windows\Microsoft.NET\assembly\GAC_32\CAXA.A5.EventSupport\v4.0_1.0.0.0__207da9a5ab81a7c8\CAXA.A5.EventSupport.dll"
)
$frameworkRefs = @(
    "C:\Windows\Microsoft.NET\Framework\v4.0.30319\System.Web.Extensions.dll",
    "C:\Windows\Microsoft.NET\Framework\v4.0.30319\WPF\UIAutomationClient.dll",
    "C:\Windows\Microsoft.NET\Framework\v4.0.30319\WPF\UIAutomationTypes.dll"
)
$missingVendorCaxa = @(
    $caxaRefNames | Where-Object { -not (Test-Path -LiteralPath (Join-Path $vendorDir $_) -PathType Leaf) }
)
$refs = @(
    if ($missingVendorCaxa.Count -eq 0) { $vendorCaxaRefs } else { $installedCaxaRefs }
    $frameworkRefs
)

if (-not (Test-Path $cs)) { throw "找不到源码：$cs" }
Write-Host "源码：$cs"
$sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $cs).Hash.ToLowerInvariant()
Write-Host "源码 SHA256：$sourceHash"

$missing = @()
foreach ($ref in $refs) {
    if (-not (Test-Path $ref)) { $missing += $ref }
}

if ($missing.Count -gt 0) {
    Write-Host "`n以下 CAXA/.NET 引用不存在：" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  $_" }
    if ($PlanOnly) {
        Write-Host "`nPlanOnly：未编译。请在工作设备上按实际安装路径修正 build_helper.ps1 的 `$refs。" -ForegroundColor Yellow
        exit 0
    }
    throw "缺少 CAXA 2011 引用 DLL，无法编译 helper。本机不探测/不安装 CAXA；请在工作设备上构建。"
}

if (-not (Test-Path $csc)) { throw "找不到 csc.exe：$csc" }

if ($PlanOnly) {
    Write-Host "`nPlanOnly：依赖齐全，可编译（未执行）。" -ForegroundColor Green
    exit 0
}

if (Test-Path $out) {
    if (-not $Force) {
        throw "已存在 $out。若确认要用当前源码重建，请加 -Force（避免把旧 exe 冒充新版本）。"
    }
    $backup = "$out.bak-" + (Get-Date -Format "yyyyMMddHHmmss")
    Copy-Item $out $backup
    Write-Host "已备份旧 exe：$backup"
}

$arguments = @("/nologo", "/platform:x86", "/out:$out")
$icon = Join-Path $PSScriptRoot "..\..\assets\app-icon.ico"
if (Test-Path -LiteralPath $icon -PathType Leaf) {
    $arguments += "/win32icon:$icon"
}
foreach ($ref in $refs) { $arguments += "/r:$ref" }
$arguments += $cs

& $csc @arguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $out)) {
    throw "编译失败（退出码 $LASTEXITCODE）"
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $cs).Hash.ToLowerInvariant() -ne $sourceHash) {
    throw "编译期间 helper 源码发生变化，请重新编译。"
}
$exeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $out).Hash.ToLowerInvariant()
New-Item -ItemType Directory -Path (Split-Path $buildRecord -Parent) -Force | Out-Null
@{ source_sha256 = $sourceHash; exe_sha256 = $exeHash } |
    ConvertTo-Json |
    Set-Content -LiteralPath $buildRecord -Encoding ASCII

Write-Host "`n编译成功：$out"
Write-Host "exe SHA256：$exeHash"
Write-Host "发布前请在真实 CAXA 客户端上做一次小批验收（见 IMPLEMENTATION.md）。"
