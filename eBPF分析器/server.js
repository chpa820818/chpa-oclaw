const express = require('express');
const { execSync } = require('child_process');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3001;

app.use(express.json());
app.use(express.static(path.join(__dirname)));

// BCC tool command mappings with fallbacks
const probeCommands = {
  // CPU & Scheduler
  execsnoop:   (d) => `timeout ${d} execsnoop-bpfcc 2>/dev/null || timeout ${d} bpftrace -e 'tracepoint:syscalls:sys_enter_execve { printf("%-8d %-6d %-16s %s\\n", elapsed/1e9, pid, comm, str(args->filename)); }' 2>/dev/null || ps aux --sort=-pcpu | head -20`,
  exitsnoop:   (d) => `timeout ${d} exitsnoop-bpfcc 2>/dev/null || ps -eo pid,ppid,cmd,etime --sort=-etime | head -20`,
  runqlat:     (d) => `timeout ${d} runqlat-bpfcc 2>/dev/null || vmstat 1 ${d} 2>/dev/null`,
  cpudist:     (d) => `timeout ${d} cpudist-bpfcc 2>/dev/null || mpstat -P ALL 1 ${d} 2>/dev/null || top -bn1 | head -20`,
  softirqs:    (d) => `timeout ${d} softirqs-bpfcc 2>/dev/null || cat /proc/softirqs`,
  hardirqs:    (d) => `timeout ${d} hardirqs-bpfcc 2>/dev/null || cat /proc/interrupts | head -20`,
  pidpersec:   (d) => `timeout ${d} pidpersec-bpfcc 2>/dev/null || for i in $(seq 1 ${d}); do echo "$(date +%H:%M:%S) PIDs: $(ls /proc | grep -c '^[0-9]')"; sleep 1; done`,
  syscount:    (d) => `timeout ${d} syscount-bpfcc 2>/dev/null || strace -c -p 1 2>&1 | head -30 || echo "syscount: 需要 bcc-tools 或 strace"`,
  // Memory
  memleak:     (d) => `timeout ${d} memleak-bpfcc 2>/dev/null || echo "== 内存概况 ==\\n$(free -h)\\n\\n== 内存详情 ==\\n$(cat /proc/meminfo | head -20)"`,
  oomkill:     (d) => `timeout ${d} oomkill-bpfcc 2>/dev/null || dmesg | grep -i "oom\\|out of memory" | tail -10 || echo "近期无 OOM 事件"`,
  slabratetop: (d) => `timeout ${d} slabratetop-bpfcc 2>/dev/null || slabtop -o -s c 2>/dev/null | head -20 || cat /proc/slabinfo | head -20`,
  cachestat:   (d) => `timeout ${d} cachestat-bpfcc 2>/dev/null || echo "== 缓存统计 ==\\n$(vmstat -s | grep -i cache)\\n\\n$(cat /proc/meminfo | grep -iE 'cache|buffer|swap')"`,
  drsnoop:     (d) => `timeout ${d} drsnoop-bpfcc 2>/dev/null || vmstat 1 ${d} 2>/dev/null`,
  // Disk I/O
  biolatency:  (d) => `timeout ${d} biolatency-bpfcc 2>/dev/null || iostat -x 1 ${d} 2>/dev/null || echo "$(iostat -d 2>/dev/null)"`,
  biosnoop:    (d) => `timeout ${d} biosnoop-bpfcc 2>/dev/null || iostat -xp 1 ${d} 2>/dev/null`,
  biotop:      (d) => `timeout ${d} biotop-bpfcc 2>/dev/null || iotop -b -n2 2>/dev/null || iostat -x 1 ${d} 2>/dev/null`,
  bitesize:    (d) => `timeout ${d} bitesize-bpfcc 2>/dev/null || echo "== I/O Stats ==\\n$(iostat -d 2>/dev/null)"`,
  // Filesystem
  opensnoop:   (d) => `timeout ${d} opensnoop-bpfcc 2>/dev/null || timeout ${d} bpftrace -e 'tracepoint:syscalls:sys_enter_openat { printf("%-6d %-16s %s\\n", pid, comm, str(args->filename)); }' 2>/dev/null || lsof 2>/dev/null | tail -20`,
  statsnoop:   (d) => `timeout ${d} statsnoop-bpfcc 2>/dev/null || echo "statsnoop: 需要 bcc-tools"`,
  filelife:    (d) => `timeout ${d} filelife-bpfcc 2>/dev/null || find /tmp -maxdepth 1 -mmin -5 -ls 2>/dev/null | head -20`,
  fileslower:  (d) => `timeout ${d} fileslower-bpfcc 2>/dev/null || echo "fileslower: 需要 bcc-tools"`,
  vfscount:    (d) => `timeout ${d} vfscount-bpfcc 2>/dev/null || echo "vfscount: 需要 bcc-tools"`,
  // Network
  tcpconnect:  (d) => `timeout ${d} tcpconnect-bpfcc 2>/dev/null || ss -tnp 2>/dev/null | head -30`,
  tcpaccept:   (d) => `timeout ${d} tcpaccept-bpfcc 2>/dev/null || ss -tlnp 2>/dev/null`,
  tcpretrans:  (d) => `timeout ${d} tcpretrans-bpfcc 2>/dev/null || netstat -s 2>/dev/null | grep -iE "retrans|segment" | head -10 || ss -ti 2>/dev/null | grep -i retrans | head -10`,
  tcplife:     (d) => `timeout ${d} tcplife-bpfcc 2>/dev/null || ss -tnp 2>/dev/null`,
  tcptop:      (d) => `timeout ${d} tcptop-bpfcc 2>/dev/null || ss -tnpi 2>/dev/null | head -30`,
  // Security
  capable:     (d) => `timeout ${d} capable-bpfcc 2>/dev/null || echo "capable: 需要 bcc-tools\\n\\n== 当前进程权限 ==\\n$(cat /proc/self/status | grep Cap)"`,
  bashreadline:(d) => `timeout ${d} bashreadline-bpfcc 2>/dev/null || echo "bashreadline: 需要 bcc-tools\\n\\n== 最近bash历史 ==\\n$(tail -20 ~/.bash_history 2>/dev/null || echo '无历史')"`,
  killsnoop:   (d) => `timeout ${d} killsnoop-bpfcc 2>/dev/null || echo "killsnoop: 需要 bcc-tools"`,
  ttysnoop:    (d) => `timeout ${d} ttysnoop-bpfcc 2>/dev/null || who 2>/dev/null`,
  // Application
  funccount:   (d) => `timeout ${d} funccount-bpfcc 'vfs_*' 2>/dev/null || echo "funccount: 需要 bcc-tools\\n\\n== 系统调用统计 ==\\n$(cat /proc/stat | head -5)"`,
  funclatency: (d) => `timeout ${d} funclatency-bpfcc 2>/dev/null || echo "funclatency: 需要 bcc-tools"`,
  stackcount:  (d) => `timeout ${d} stackcount-bpfcc 2>/dev/null || echo "stackcount: 需要 bcc-tools"`,
  trace:       (d) => `timeout ${d} trace-bpfcc 2>/dev/null || echo "trace: 需要 bcc-tools\\n\\n== 可用 tracepoints ==\\n$(ls /sys/kernel/debug/tracing/events/ 2>/dev/null | head -20 || echo '无法访问 tracing')"`,
  argdist:     (d) => `timeout ${d} argdist-bpfcc 2>/dev/null || echo "argdist: 需要 bcc-tools"`,
};

// Run probe
app.post('/api/ebpf', (req, res) => {
  const { probe, duration = 5 } = req.body;
  const dur = Math.min(Math.max(parseInt(duration) || 5, 1), 60);
  const cmdFn = probeCommands[probe];
  if (!cmdFn) return res.json({ error: `未知探针: ${probe}` });
  
  const startTime = Date.now();
  try {
    const output = execSync(cmdFn(dur), { timeout: (dur + 10) * 1000, maxBuffer: 2 * 1024 * 1024 }).toString();
    const elapsed = (Date.now() - startTime) / 1000;
    const lines = output.split('\n').filter(l => l.trim()).length;
    res.json({ output, lines, duration: elapsed, probe });
  } catch (e) {
    const output = e.stdout ? e.stdout.toString() : (e.stderr ? e.stderr.toString() : e.message);
    const elapsed = (Date.now() - startTime) / 1000;
    const lines = output.split('\n').filter(l => l.trim()).length;
    res.json({ output: output || '探针执行完毕', lines, duration: elapsed, probe });
  }
});

// Stop probe
app.post('/api/ebpf/stop', (req, res) => {
  try { execSync('pkill -f bpftrace 2>/dev/null; pkill -f bpfcc 2>/dev/null', { timeout: 3000 }); } catch {}
  res.json({ ok: true });
});

app.listen(PORT, () => {
  console.log(`🐝 eBPF 分析器运行在 http://localhost:${PORT}`);
});
