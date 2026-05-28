# 录屏 GIF 工具

当前版本：**v1.0.0**

[一键下载最新版 ZIP](https://github.com/chpa820818/chpa-oclaw/releases/latest/download/screen-gif-studio-v1.0.0.zip)

一个 Windows 桌面小工具，用于录屏、转 GIF、视频拼接和视频拆分。界面使用 Python Tkinter 实现，媒体处理依赖 FFmpeg。

## 功能

### 录屏 / 转 GIF

- 全屏录制桌面并保存为 MP4
- 停止录制后可自动转换为 GIF
- 录制开始后自动隐藏工具窗口，避免被录入画面
- 支持自定义录制快捷键
  - 暂停 / 继续：默认 `Ctrl + Alt + P`
  - 停止录制：默认 `Ctrl + Alt + S`
- 录制完成后自动加载右侧预览播放器
- 支持选择已有视频并转换为 GIF

### 拼接

- 多个 MP4 / GIF / 常见视频文件按顺序拼接
- 支持调整拼接顺序
- 支持输出为 MP4 或 GIF
- 显示拼接进度
- 拼接完成后自动加载右侧视频预览区并播放

### 拆分

- 支持选择视频后读取总时长
- 支持拖动时间轴定位
- 支持点击“切一刀”添加切点
- 支持按切点导出多个片段
- 支持按手动配置导出片段
- 支持输出 MP4 或 GIF
- 显示导出进度和导出结果文件列表

### 视频预览

录屏、拼接、拆分页均使用右侧大视频预览区：

- 播放 / 暂停
- 进度条拖拽定位
- 倍速选择：`0.5x`、`1.0x`、`1.5x`、`2.0x`、`3.0x`
- 清晰度选择：普通、高清、超清
- 刷新当前帧
- 放大预览

> 当前预览基于 FFmpeg 抽帧模拟播放，不是原生视频播放器。优点是依赖少，缺点是播放流畅度不等同于系统播放器。

## 环境要求

- Windows
- Python 3.10+
- FFmpeg / FFprobe

Tkinter 是 Python 标准库的一部分，通常无需额外安装。

## 安装 FFmpeg

可以在工具界面中点击 **安装 FFmpeg**，或在当前目录运行：

```powershell
.\install-ffmpeg.ps1
```

该脚本使用 `winget` 安装 `Gyan.FFmpeg`。

## 运行

```powershell
.\run.ps1
```

`run.ps1` 会过滤无害的 `libpng warning: iCCP: known incorrect sRGB profile` 警告，避免 PowerShell 将其误报为失败。

## 输出目录

默认输出到：

```text
.\output
```

即工具所在目录下的 `output` 文件夹，可在工具右上角修改输出目录。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `main.py` | 主程序 |
| `run.ps1` | 启动脚本 |
| `install-ffmpeg.ps1` | FFmpeg 安装脚本 |
| `VERSION` | 当前版本号 |

## 版本

当前发布版本：`v1.0.0`

## 注意事项

- 录制快捷键是全局热键，如果被其他程序占用，日志会提示注册失败。
- 暂停录制通过 Windows 进程挂起/恢复实现，仅支持 Windows。
- 输出 GIF 可能较大，建议控制录制时长和 GIF 宽度。
