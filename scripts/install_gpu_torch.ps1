# 使用南京大学 PyTorch 镜像安装 CUDA 版 torch（Windows + Python 3.9 + cu118）
# 适用 RTX 20/30 系列（如 RTX 2060 6GB）
# 用法: powershell -ExecutionPolicy Bypass -File scripts/install_gpu_torch.ps1

$ErrorActionPreference = "Stop"
$Mirror = "https://mirror.nju.edu.cn/pytorch/whl/cu118"
$Python = if ($env:WENSHU_PYTHON) { $env:WENSHU_PYTHON } else { "python" }

Write-Host "==> 卸载旧版 torch / torchvision / torchaudio ..."
& $Python -m pip uninstall torch torchvision torchaudio -y

Write-Host "==> 从镜像安装 CUDA 11.8 版 PyTorch 2.4.1 ..."
& $Python -m pip install torch==2.4.1 torchvision==0.19.1 torchaudio==2.4.1 --index-url $Mirror

Write-Host "==> 验证 GPU ..."
& $Python -c @"
import torch
print('torch', torch.__version__)
print('cuda_available', torch.cuda.is_available())
if torch.cuda.is_available():
    print('device', torch.cuda.get_device_name(0))
    p = torch.cuda.get_device_properties(0)
    print('vram_gb', round(p.total_memory / 1024**3, 1))
"@

Write-Host "完成。请重启问数平台后，在「索引状态」确认显示 GPU。"
