# 🐝 eBPF 分析器

基于 [BCC (BPF Compiler Collection)](https://github.com/iovisor/bcc) 的内核级动态追踪工具 Web UI。

## 功能

覆盖 BCC 工具全景图的 **7 大观测层**，共 **39 个追踪工具**：

| 分类 | 工具 | 说明 |
|------|------|------|
| ⚡ CPU & 调度 | execsnoop, exitsnoop, runqlat, cpudist, softirqs, hardirqs, pidpersec, syscount | 进程追踪、调度延迟、中断分析 |
| 🧠 内存 | memleak, oomkill, slabratetop, cachestat, drsnoop | 内存泄漏、OOM、缓存命中率 |
| 💾 磁盘 I/O | biolatency, biosnoop, biotop, bitesize | I/O延迟、吞吐排行 |
| 📂 文件系统 | opensnoop, statsnoop, filelife, fileslower, vfscount | 文件操作追踪 |
| 🌐 网络 | tcpconnect, tcpaccept, tcpretrans, tcplife, tcptop | TCP连接、重传、吞吐 |
| 🛡️ 安全审计 | capable, bashreadline, killsnoop, ttysnoop | 权限检查、命令审计 |
| 🔧 应用层 | funccount, funclatency, stackcount, trace, argdist | 函数追踪、调用栈分析 |

## 界面特性

- 📂 左右分栏布局：左侧工具列表 / 右侧详情+终端
- 🏷️ 每个工具标注探针类型 (kprobe/tracepoint/uretprobe) 和性能开销等级
- 🔗 一键跳转 BCC 源码
- 📟 终端风格实时输出
- ⏱️ 可配置采样时长 (3/5/10/30秒)
- 🔄 BCC 不可用时自动降级到传统工具 (iostat/vmstat/ss/ps 等)

## 运行

```bash
# 安装依赖
cd eBPF分析器
npm install

# 启动
npm start
# 或
node server.js

# 访问
open http://localhost:3001
```

## 前置条件

- **Node.js** >= 16
- **Linux** 内核 >= 4.1 (eBPF 支持)
- **BCC Tools** (可选，推荐安装以获取完整功能)

```bash
# Ubuntu/Debian 安装 BCC
sudo apt install bpfcc-tools linux-headers-$(uname -r)

# CentOS/RHEL
sudo yum install bcc-tools
```

如果未安装 BCC，工具会自动降级使用传统 Linux 命令 (iostat, vmstat, ss, ps 等) 作为替代。

## 架构

```
eBPF分析器/
├── index.html     # 前端单页应用 (暗色赛博朋克风格)
├── server.js      # 后端 Express API (探针执行 + 降级逻辑)
├── package.json
└── README.md
```

## 技术参考

- [iovisor/bcc](https://github.com/iovisor/bcc) - BCC 工具集
- [BPF Performance Tools](https://www.brendangregg.com/bpf-performance-tools-book.html) - Brendan Gregg
- [bcc_tracing_tools_2019.png](https://github.com/iovisor/bcc/blob/master/images/bcc_tracing_tools_2019.png) - BCC 工具全景图

## License

MIT
