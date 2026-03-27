const express = require('express');
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

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

// Generate custom trace script using AI
app.post('/api/ebpf/generate', async (req, res) => {
  const { prompt } = req.body;
  if (!prompt) return res.json({ error: '请输入追踪需求描述' });

  // Reference trace examples for context
  const examples = `
已有的 BCC/bpftrace 工具示例供参考：

1. execsnoop - 追踪进程创建:
   bpftrace -e 'tracepoint:syscalls:sys_enter_execve { printf("%-8d %-16s %s\\n", pid, comm, str(args->filename)); }'

2. opensnoop - 追踪文件打开:
   bpftrace -e 'tracepoint:syscalls:sys_enter_openat { printf("%-6d %-16s %s\\n", pid, comm, str(args->filename)); }'

3. biolatency - 磁盘I/O延迟:
   bpftrace -e 'tracepoint:block:block_rq_issue { @start[args->dev,args->sector]=nsecs; } tracepoint:block:block_rq_complete /@start[args->dev,args->sector]/ { @usecs=hist((nsecs-@start[args->dev,args->sector])/1000); delete(@start[args->dev,args->sector]); }'

4. tcpconnect - TCP连接追踪:
   bpftrace -e 'kprobe:tcp_connect { printf("%-6d %-16s\\n", pid, comm); }'

5. 按进程统计VFS读取字节数:
   bpftrace -e 'kprobe:vfs_read { @bytes[comm] = sum(arg2); } interval:s:1 { print(@bytes); clear(@bytes); }'

6. 追踪特定文件访问:
   bpftrace -e 'tracepoint:syscalls:sys_enter_openat /str(args->filename) == "/etc/passwd"/ { printf("%-6d %-16s\\n", pid, comm); }'

7. 统计系统调用:
   bpftrace -e 'tracepoint:raw_syscalls:sys_enter { @[comm] = count(); } interval:s:5 { print(@); clear(@); }'
`;

  const systemPrompt = `你是一个 eBPF/BCC 专家。用户会用自然语言描述他们想追踪的内容，你需要生成对应的 bpftrace 脚本。

规则：
1. 优先使用 bpftrace 一行脚本格式
2. 如果需求很复杂，可以生成多行 bpftrace 脚本
3. 如果 bpftrace 无法实现，生成等效的 shell 命令组合
4. 脚本必须可以直接执行，不需要额外安装
5. 考虑安全性，不要生成可能导致系统不稳定的脚本
6. 输出的时间间隔使用 interval:s:1 保持实时性

${examples}

请严格按以下 JSON 格式回复（不要包含 markdown 代码块标记）：
{"type":"bpftrace或shell","script":"完整的可执行脚本","explanation":"简要说明这个脚本做了什么，追踪了什么探针，输出什么信息"}`;

  try {
    // Use the copilot proxy to call AI
    const tokenPath = path.join(process.env.HOME, '.openclaw/credentials/github-copilot.token.json');
    let token = '';
    try {
      const tokenData = JSON.parse(fs.readFileSync(tokenPath, 'utf8'));
      token = tokenData.token;
    } catch {
      // Try copilot proxy on localhost:4399
    }

    const aiResp = await fetch('http://localhost:4399/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({
        model: 'claude-sonnet-4',
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: prompt }
        ],
        max_tokens: 2000,
        temperature: 0.3
      }),
      signal: AbortSignal.timeout(30000)
    });
    const aiData = await aiResp.json();
    const content = aiData.choices?.[0]?.message?.content || '';
    
    // Parse JSON response
    let parsed;
    try {
      // Try to extract JSON from response (handle markdown code blocks)
      const jsonMatch = content.match(/\{[\s\S]*\}/);
      parsed = JSON.parse(jsonMatch ? jsonMatch[0] : content);
    } catch {
      // Fallback: treat as raw script
      parsed = { type: 'bpftrace', script: content, explanation: '已生成脚本' };
    }

    res.json({
      script: parsed.script || content,
      type: parsed.type || 'bpftrace',
      explanation: parsed.explanation || ''
    });
  } catch (e) {
    // Fallback: generate script locally without AI
    const fallback = generateFallbackScript(prompt);
    if (fallback) {
      res.json(fallback);
    } else {
      res.json({ error: 'AI 生成失败: ' + e.message });
    }
  }
});

// Fallback script generation (no AI needed)
function generateFallbackScript(prompt) {
  const p = prompt.toLowerCase();
  if (p.includes('passwd') || p.includes('密码')) {
    return { type:'bpftrace', script:`bpftrace -e 'tracepoint:syscalls:sys_enter_openat /str(args->filename) == "/etc/passwd"/ { printf("%-8d %-16s %s\\n", pid, comm, str(args->filename)); }'`, explanation:'追踪所有尝试打开 /etc/passwd 的进程' };
  }
  if (p.includes('dns') || p.includes('域名') || p.includes('53')) {
    return { type:'shell', script:`timeout $DUR tcpdump -i any -nn port 53 2>/dev/null || ss -unp | grep ':53'`, explanation:'捕获 DNS 查询流量（UDP 53端口）' };
  }
  if (p.includes('系统调用') || p.includes('syscall')) {
    return { type:'bpftrace', script:`bpftrace -e 'tracepoint:raw_syscalls:sys_enter { @[comm] = count(); } interval:s:1 { print(@); clear(@); }'`, explanation:'按进程统计每秒系统调用次数' };
  }
  if (p.includes('慢') && (p.includes('io') || p.includes('磁盘') || p.includes('i/o'))) {
    return { type:'bpftrace', script:`bpftrace -e 'tracepoint:block:block_rq_issue { @start[args->dev,args->sector]=nsecs; } tracepoint:block:block_rq_complete /@start[args->dev,args->sector]/ { $lat=(nsecs-@start[args->dev,args->sector])/1e6; if($lat>50){printf("SLOW IO: %dms dev=%d comm=%s\\n",$lat,args->dev,comm);} delete(@start[args->dev,args->sector]); }'`, explanation:'追踪超过50ms的磁盘I/O请求' };
  }
  if (p.includes('tcp') && p.includes('rst')) {
    return { type:'shell', script:`timeout $DUR tcpdump -i any 'tcp[tcpflags] & tcp-rst != 0' -nn 2>/dev/null || ss -tnp | head -20`, explanation:'捕获所有 TCP RST 包' };
  }
  if (p.includes('node') && (p.includes('文件') || p.includes('file'))) {
    return { type:'bpftrace', script:`bpftrace -e 'tracepoint:syscalls:sys_enter_openat /comm == "node"/ { printf("%-6d %s\\n", pid, str(args->filename)); }'`, explanation:'追踪 Node.js 进程的文件打开操作' };
  }
  if (p.includes('内存') || p.includes('memory') || p.includes('分配')) {
    return { type:'shell', script:`echo "== 内存概况 =="; free -h; echo ""; echo "== 进程内存排行 =="; ps aux --sort=-rss | head -15; echo ""; echo "== 内存详情 =="; cat /proc/meminfo | head -20`, explanation:'查看内存使用概况和进程排行' };
  }
  if (p.includes('网络') || p.includes('连接') || p.includes('connect')) {
    return { type:'bpftrace', script:`bpftrace -e 'kprobe:tcp_connect { printf("%-6d %-16s -> connect()\\n", pid, comm); }'`, explanation:'追踪所有出站 TCP 连接' };
  }
  return null;
}

// Run custom script
app.post('/api/ebpf/run-custom', (req, res) => {
  let { script, duration = 5 } = req.body;
  if (!script) return res.json({ error: '脚本为空' });
  const dur = Math.min(Math.max(parseInt(duration) || 5, 1), 60);

  // Replace $DUR placeholder with actual duration
  script = script.replace(/\$DUR/g, dur.toString());

  // Safety check: reject obviously dangerous commands
  const dangerous = ['rm -rf', 'mkfs', 'dd if=', '> /dev/', 'chmod 777', 'fork bomb', ':(){ :|:& };:'];
  for (const d of dangerous) {
    if (script.includes(d)) return res.json({ error: `安全检查失败：脚本包含危险命令 "${d}"` });
  }

  // Wrap with timeout
  const cmd = `timeout ${dur + 5} bash -c '${script.replace(/'/g, "'\\''")}'`;

  const startTime = Date.now();
  try {
    const output = execSync(cmd, { timeout: (dur + 10) * 1000, maxBuffer: 2 * 1024 * 1024 }).toString();
    const elapsed = (Date.now() - startTime) / 1000;
    const lines = output.split('\n').filter(l => l.trim()).length;
    res.json({ output, lines, duration: elapsed });
  } catch (e) {
    const output = e.stdout ? e.stdout.toString() : (e.stderr ? e.stderr.toString() : e.message);
    const elapsed = (Date.now() - startTime) / 1000;
    const lines = output.split('\n').filter(l => l.trim()).length;
    res.json({ output: output || '脚本执行完毕', lines, duration: elapsed });
  }
});

app.listen(PORT, () => {
  console.log(`🐝 eBPF 分析器运行在 http://localhost:${PORT}`);
});
