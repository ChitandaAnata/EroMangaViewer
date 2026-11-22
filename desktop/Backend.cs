using System;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;

namespace MangaShelf;

/// <summary>
/// 拉起 / 复用本机的 Python 后端。外壳只负责找端口、启动、等就绪、退出时收尾。
/// 所有业务逻辑（扫描、缩略图、标签、进度）都在后端，和网页端用的是同一份代码。
/// </summary>
internal sealed class Backend : IDisposable
{
    private const int FirstPort = 8791;
    private const int LastPort = 8810;

    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(3) };

    private Process? _proc;
    private IntPtr _job;
    private readonly string _root;

    /// <summary>后端最后几行错误输出，给启动失败时的提示用</summary>
    private readonly System.Collections.Generic.List<string> _recentError = new();

    public string LastError
    {
        get
        {
            lock (_recentError)
            {
                return _recentError.Count == 0 ? "" : string.Join(Environment.NewLine, _recentError);
            }
        }
    }

    public int Port { get; private set; }
    public string BaseUrl => $"http://127.0.0.1:{Port}";

    public Backend(string root)
    {
        _root = root;
    }

    /// <summary>
    /// 开发模式：有源码（app.py）但没有打包好的 backend\MangaShelfServer.exe 时，
    /// 直接拿 Python 跑源码里的 app.py。这样双击源码目录里的 MangaShelf.exe 就是「当前版本」，
    /// 改 server/ 重启一次即可，改 web/ 按 F5 就生效。
    /// 装好的版本两者都有，走 exe。MANGASHELF_DEV=1 可强制开启，=0 可强制关闭。
    /// </summary>
    private bool DevMode
    {
        get
        {
            var v = Environment.GetEnvironmentVariable("MANGASHELF_DEV");
            if (!string.IsNullOrWhiteSpace(v))
            {
                return v != "0" && !v.Equals("false", StringComparison.OrdinalIgnoreCase);
            }

            return !File.Exists(AppPaths.BackendExe)
                && File.Exists(Path.Combine(_root, "app.py"));
        }
    }

    /// <summary>开发模式下找 Python：先看 MANGASHELF_PYTHON，再找常见安装位置，最后交给 PATH</summary>
    private static string FindPython()
    {
        var custom = Environment.GetEnvironmentVariable("MANGASHELF_PYTHON");
        if (!string.IsNullOrWhiteSpace(custom) && File.Exists(custom)) return custom;

        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var candidates = new System.Collections.Generic.List<string>();
        foreach (var ver in new[] { "313", "312", "311", "310", "39" })
        {
            if (!string.IsNullOrWhiteSpace(local))
                candidates.Add(Path.Combine(local, "Programs", "Python", "Python" + ver, "python.exe"));
        }
        candidates.Add(@"D:\python\python.exe");
        candidates.Add(@"C:\Python313\python.exe");
        candidates.Add(@"C:\Python312\python.exe");

        foreach (var p in candidates)
        {
            if (File.Exists(p)) return p;
        }

        // 都没找到就交给 PATH，起不来的话日志里会看到「启动后端失败」
        return "python";
    }

    /// <summary>找一条能用的路：先看有没有已经跑着的「本目录服务」，否则自己拉一个</summary>
    public async Task<bool> StartAsync(Action<string> log)
    {
        string? program;
        if (DevMode)
        {
            program = FindPython();
            if (!File.Exists(Path.Combine(_root, "app.py")))
            {
                log("开发模式：根目录下没有 app.py：" + _root);
                return false;
            }
            log("开发模式：源码后端 " + program + " app.py");
        }
        else
        {
            program = AppPaths.BackendExe;
            if (!File.Exists(program))
            {
                log("找不到后端程序：" + program);
                return false;
            }
        }

        for (var port = FirstPort; port <= LastPort; port++)
        {
            if (await IsOurServerAsync(port))
            {
                Port = port;
                log($"复用已在运行的服务 :{port}");
                return true;
            }
            if (await PortBusyAsync(port)) continue;

            if (!StartProcess(program, port, log)) continue;

            // 后端起来要初始化解压库、数据库，正常几秒；给 20 秒，超了换端口
            for (var i = 0; i < 40; i++)
            {
                await Task.Delay(500);
                if (await IsOurServerAsync(port))
                {
                    Port = port;
                    log($"服务就绪 :{port}");
                    return true;
                }
                if (_proc is { HasExited: true })
                {
                    log($"后端进程已退出（exit={_proc.ExitCode}），换下一个端口");
                    Cleanup();
                    break;
                }
            }
        }

        log("没有可用端口，或者后端一直没起来");
        return false;
    }

    private bool StartProcess(string program, int port, Action<string> log)
    {
        try
        {
            var psi = new ProcessStartInfo(program)
            {
                WorkingDirectory = _root,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                // 后端按 UTF-8 输出（下面设了 PYTHONIOENCODING），
                // 不显式指定的话这里按控制台代码页解码，中文全成乱码
                StandardOutputEncoding = System.Text.Encoding.UTF8,
                StandardErrorEncoding = System.Text.Encoding.UTF8,
            };
            if (DevMode)
            {
                psi.ArgumentList.Add("-u");   // 不缓冲，日志能实时打到启动日志里
                psi.ArgumentList.Add(Path.Combine(_root, "app.py"));
            }
            psi.ArgumentList.Add("--port");
            psi.ArgumentList.Add(port.ToString());
            psi.ArgumentList.Add("--host");
            psi.ArgumentList.Add("127.0.0.1");
            psi.ArgumentList.Add("--no-browser");
            psi.ArgumentList.Add("--exact-port");
            // 程序根目录决定 web/ 从哪读；数据目录单独给（可能落在用户目录）
            psi.EnvironmentVariables["MANGASHELF_HOME"] = _root;
            psi.EnvironmentVariables["MANGASHELF_DATA"] = AppPaths.DataDir;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";

            var proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
            proc.OutputDataReceived += (_, e) => { if (!string.IsNullOrWhiteSpace(e.Data)) log("  " + e.Data); };
            proc.ErrorDataReceived += (_, e) =>
            {
                if (string.IsNullOrWhiteSpace(e.Data)) return;
                log("  ! " + e.Data);
                lock (_recentError)
                {
                    _recentError.Add(e.Data.Trim());
                    if (_recentError.Count > 4) _recentError.RemoveAt(0);
                }
            };

            if (!proc.Start()) return false;
            proc.BeginOutputReadLine();
            proc.BeginErrorReadLine();
            _proc = proc;
            AttachJob(proc);
            log($"启动后端 pid={proc.Id} :{port}");
            return true;
        }
        catch (Exception ex)
        {
            log("启动后端失败：" + ex.Message);
            return false;
        }
    }

    public async Task<bool> IsOurServerAsync(int port)
    {
        try
        {
            using var resp = await Http.GetAsync($"http://127.0.0.1:{port}/api/identity");
            if (!resp.IsSuccessStatusCode) return false;
            using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
            var root = doc.RootElement.TryGetProperty("root", out var r) ? (r.GetString() ?? "") : "";
            return string.Equals(root.TrimEnd('\\'), _root.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return false;
        }
    }

    private static async Task<bool> PortBusyAsync(int port)
    {
        try
        {
            using var client = new TcpClient();
            await client.ConnectAsync(System.Net.IPAddress.Loopback, port);
            return true;
        }
        catch
        {
            return false;
        }
    }

    private void Cleanup()
    {
        var proc = _proc;
        _proc = null;
        if (proc is null) return;
        try
        {
            if (!proc.HasExited) proc.Kill(true);
        }
        catch { }
        try { proc.Dispose(); } catch { }
    }

    public void Dispose()
    {
        Cleanup();
        if (_job != IntPtr.Zero)
        {
            // 关闭 job 句柄时，作业里的进程会一起结束，不留孤儿后端
            try { CloseHandle(_job); } catch { }
            _job = IntPtr.Zero;
        }
    }

    // ---------------------------------------------------------------- 作业对象
    // 外壳被任务管理器强杀时，后端不能还占着端口在后台跑。把它放进一个
    // 「句柄关闭即杀进程」的作业对象里，外壳一死后端跟着走。

    private void AttachJob(Process proc)
    {
        try
        {
            if (_job == IntPtr.Zero) _job = CreateJobObject(IntPtr.Zero, null);
            if (_job == IntPtr.Zero) return;

            var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION
            {
                BasicLimitInformation = new JOBOBJECT_BASIC_LIMIT_INFORMATION
                {
                    LimitFlags = 0x2000, // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                },
            };
            var len = Marshal.SizeOf<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>();
            var ptr = Marshal.AllocHGlobal(len);
            try
            {
                Marshal.StructureToPtr(info, ptr, false);
                SetInformationJobObject(_job, 9, ptr, (uint)len);
            }
            finally
            {
                Marshal.FreeHGlobal(ptr);
            }

            AssignProcessToJobObject(_job, proc.Handle);
        }
        catch
        {
            /* 加不进作业也不影响使用，只是强杀时可能留个后端进程 */
        }
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string? name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr hJob, int infoClass, IntPtr lpInfo, uint cbInfo);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr h);

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }
}
