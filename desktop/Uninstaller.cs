using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace MangaShelf;

/// <summary>
/// 卸载入口。安装目录里的 Uninstall.exe 与 MangaShelf.exe 是同一个文件（硬链接），
/// 点它或者在「应用和功能」里点卸载都会走到这里。
///
/// 进程删不掉自己，所以先把 exe 复制一份到临时目录，由副本负责整个删除过程，
/// 安装目录里除了用户数据之外一个文件都不剩。
/// </summary>
internal static class Uninstaller
{
    public const string AppName = "MangaShelf";
    public const string ExeName = "MangaShelf.exe";
    public const string ServerName = "MangaShelfServer.exe";
    public const string UninstallName = "Uninstall.exe";
    public const string RegPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\MangaShelf";

    /// <summary>
    /// 卸载用的临时目录固定在 %LOCALAPPDATA%\Temp，绝不放在安装目录里，
    /// 否则「连数据一起删」时会把正在运行的副本自己删掉。
    /// </summary>
    public static void PrepareTemp()
    {
        try
        {
            var dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Temp", "MangaShelf-uninstall");
            Directory.CreateDirectory(dir);
            Environment.SetEnvironmentVariable("TEMP", dir, EnvironmentVariableTarget.Process);
            Environment.SetEnvironmentVariable("TMP", dir, EnvironmentVariableTarget.Process);
        }
        catch
        {
            /* 挪不动就按系统默认来 */
        }
    }

    /// <summary>当前进程是否管理员。装在 Program Files 时删目录必须有它。</summary>
    private static bool IsElevated
    {
        get
        {
            try
            {
                using var identity = WindowsIdentity.GetCurrent();
                return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
            }
            catch
            {
                return false;
            }
        }
    }

    /// <summary>安装目录能不能删改。写一个探针再删掉，比查 ACL 可靠。</summary>
    private static bool CanWriteIn(string dir)
    {
        try
        {
            var probe = Path.Combine(dir, "probe-" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".tmp");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);
            return true;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// 权限不够时用 UAC 把自己重启一次（标准卸载程序的做法）。
    /// 返回 true 表示活已经交给提权后的新进程，本进程应当直接退出。
    /// </summary>
    private static bool TryElevate(string dir, bool silent, bool purge)
    {
        var exe = Environment.ProcessPath;
        if (string.IsNullOrWhiteSpace(exe)) return false;

        var psi = new ProcessStartInfo(exe) { UseShellExecute = true, Verb = "runas" };
        psi.ArgumentList.Add("--uninstall");
        psi.ArgumentList.Add("--dir=" + dir);
        if (purge) psi.ArgumentList.Add("--purge");
        if (silent) psi.ArgumentList.Add("/S");

        try
        {
            using var child = Process.Start(psi);
            if (child is null) return false;

            if (silent)
            {
                child.WaitForExit();
                Environment.ExitCode = child.ExitCode;
            }
            return true;
        }
        catch (Win32Exception ex) when (ex.NativeErrorCode == 1223)
        {
            return false;   // 用户取消了 UAC
        }
        catch
        {
            return false;
        }
    }

    public static void Run(string[] args)
    {
        var silent = args.Any(a =>
            a.Equals("/S", StringComparison.OrdinalIgnoreCase) ||
            a.Equals("--silent", StringComparison.OrdinalIgnoreCase));
        // 默认保留 data（配置、阅读进度）；带 --purge 才连数据一起删
        var purge = args.Any(a => a.Equals("--purge", StringComparison.OrdinalIgnoreCase));

        var dir = (ArgValue(args, "--dir=") ?? AppContext.BaseDirectory)
            .Trim()
            .Trim('"')
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        if (dir.Length == 0) return;

        // 装在 C:\Program Files 这类位置时，普通权限删不动，先要一次管理员权限
        var fromTemp = args.Any(a => a.Equals("--from-temp", StringComparison.OrdinalIgnoreCase));
        if (!fromTemp && !IsElevated && !CanWriteIn(dir) && TryElevate(dir, silent, purge))
        {
            return;
        }

        // 第一次进来先把自己搬去临时目录，让安装目录里的 exe 能被删除
        if (!args.Any(a => a.Equals("--from-temp", StringComparison.OrdinalIgnoreCase))
            && SpawnCopy(dir, silent, purge))
        {
            return;
        }

        if (silent)
        {
            WaitForParent(args);
            Job.Run(dir, !purge, null);
            ScheduleSelfCleanup();
            return;
        }

        WaitForParent(args);
        using (var form = new UninstallForm(dir))
        {
            Application.Run(form);
        }
        ScheduleSelfCleanup();
    }

    /// <summary>
    /// 副本要等原进程真的退出再动手：
    /// 固定 sleep 靠猜，猜短了 Uninstall.exe 还被镜像锁着删不掉，会留下一个 48MB 的残骸。
    /// </summary>
    private static void WaitForParent(string[] args)
    {
        var raw = ArgValue(args, "--wait-pid=");
        if (!int.TryParse(raw, out var pid)) return;

        try
        {
            var parent = Process.GetProcessById(pid);
            parent.WaitForExit(20000);
        }
        catch
        {
            // 已经退出了，正好
        }
        Thread.Sleep(500);
    }

    /// <summary>复制一份自己到别处并以副本重启；成功返回 true</summary>
    private static bool SpawnCopy(string dir, bool silent, bool purge)
    {
        foreach (var tempDir in CopyDirs(dir))
        {
            try
            {
                Directory.CreateDirectory(tempDir);

                // 清掉上次遗留的副本（还在运行的删不掉，忽略）
                foreach (var old in Directory.GetFiles(tempDir, "ms-uninstall-*.exe"))
                {
                    try { File.Delete(old); } catch { /* 还在用 */ }
                }

                var temp = Path.Combine(tempDir, $"ms-uninstall-{Guid.NewGuid():N}.exe");
                File.Copy(Application.ExecutablePath, temp, true);

                var psi = new ProcessStartInfo(temp)
                {
                    UseShellExecute = false,
                    WorkingDirectory = tempDir,
                };
                psi.ArgumentList.Add("--uninstall");
                psi.ArgumentList.Add("--from-temp");
                psi.ArgumentList.Add("--dir=" + dir);
                psi.ArgumentList.Add("--wait-pid=" + Environment.ProcessId);
                if (silent) psi.ArgumentList.Add("/S");
                if (purge) psi.ArgumentList.Add("--purge");

                if (Process.Start(psi) is not null) return true;
            }
            catch
            {
                /* 这个位置不行，换下一个 */
            }
        }

        // 全都失败了：只能原地干活，残留交给最后的延迟清理
        return false;
    }

    /// <summary>
    /// 副本候选位置。不能只指望 %LOCALAPPDATA%：有些机器的安全策略只让程序
    /// 写自己那一亩三分地，AppData 直接拒绝访问。父目录和安装目录内部都要试。
    /// </summary>
    private static IEnumerable<string> CopyDirs(string dir)
    {
        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (!string.IsNullOrWhiteSpace(local))
        {
            yield return Path.Combine(local, "Temp", "MangaShelf-uninstall");
        }

        // 安装目录的父目录：装到 %LOCALAPPDATA%\Programs\MangaShelf 时就是 Programs
        var parent = Path.GetDirectoryName(dir.TrimEnd(Path.DirectorySeparatorChar));
        if (!string.IsNullOrWhiteSpace(parent))
        {
            yield return Path.Combine(parent, ".ms-uninstall");
        }

        // 最差也能在安装目录内部跑，至少不会自己锁自己
        yield return Path.Combine(dir, ".uninstall-temp");
    }

    /// <summary>副本文件在进程退出后删掉：延迟到重启也行，别在临时目录留 50MB</summary>
    private static void ScheduleSelfCleanup()
    {
        if (!Environment.GetCommandLineArgs()
                .Any(a => a.Equals("--from-temp", StringComparison.OrdinalIgnoreCase)))
        {
            return;
        }

        var self = Application.ExecutablePath;
        try
        {
            var psi = new ProcessStartInfo("cmd.exe")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            psi.ArgumentList.Add("/c");
            psi.ArgumentList.Add($"ping -n 3 127.0.0.1 >nul & del /f /q \"{self}\"");
            Process.Start(psi);
            return;
        }
        catch
        {
            /* 退回到重启后删除 */
        }

        try { MoveFileEx(self, null, MOVEFILE_DELAY_UNTIL_REBOOT); } catch { /* ignore */ }
    }

    private const int MOVEFILE_DELAY_UNTIL_REBOOT = 0x4;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool MoveFileEx(string existing, string? newName, int flags);

    private static string? ArgValue(string[] args, string prefix) =>
        args.FirstOrDefault(a => a.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            ?.Substring(prefix.Length);
}

/// <summary>真正的删除动作：关进程 → 删快捷方式 → 清注册表 → 删目录</summary>
internal static class Job
{
    public static void Run(string dir, bool keepData, Action<string>? report)
    {
        // 调用方（WaitForParent）已经确认上一份程序退出了，这里只留一点缓冲
        Thread.Sleep(400);

        report?.Invoke("正在关闭 MangaShelf…");
        StopProcesses(dir);

        report?.Invoke("正在删除快捷方式…");
        Shortcuts.RemoveAll();

        report?.Invoke("正在清理注册表…");
        ClearRegistry(dir);

        report?.Invoke(keepData ? "正在删除程序文件（保留配置与进度）…" : "正在删除全部文件…");
        DeleteTree(dir, keepData);

        report?.Invoke("卸载完成");

        // 还剩删不掉的东西（多半是正在运行的副本自己），交给后台延迟清理
        ScheduleLeftoverCleanup(dir, keepData);
    }

    /// <summary>
    /// 兜底清理：有些环境不让程序写 %LOCALAPPDATA%，副本只能放在安装目录里，
    /// 那么副本自己就成了残留。起一个隐藏的 PowerShell 等 5 秒再强删，
    /// 用户不会在安装目录里看到半截残留。
    /// </summary>
    private static void ScheduleLeftoverCleanup(string dir, bool keepData)
    {
        if (!Directory.Exists(dir)) return;

        // 保留数据时，正常情况下目录里只剩 data，不用再动
        if (keepData)
        {
            var others = SafeGetFiles(dir).Length > 0
                         || SafeGetDirs(dir).Any(d =>
                             !Path.GetFileName(d).Equals("data", StringComparison.OrdinalIgnoreCase));
            if (!others) return;
        }

        try
        {
            var path = dir.Replace("'", "''");
            var command = keepData
                ? $"Start-Sleep -Seconds 5; " +
                  $"Get-ChildItem -LiteralPath '{path}' -Force -ErrorAction SilentlyContinue | " +
                  $"Where-Object {{ $_.Name -ne 'data' }} | " +
                  $"Remove-Item -Recurse -Force -ErrorAction SilentlyContinue"
                : $"Start-Sleep -Seconds 5; " +
                  $"Remove-Item -LiteralPath '{path}' -Recurse -Force -ErrorAction SilentlyContinue";

            var psi = new ProcessStartInfo("powershell.exe")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            psi.ArgumentList.Add("-NoProfile");
            psi.ArgumentList.Add("-WindowStyle");
            psi.ArgumentList.Add("Hidden");
            psi.ArgumentList.Add("-Command");
            psi.ArgumentList.Add(command);
            Process.Start(psi);
        }
        catch
        {
            /* 起不来就留着，下次重装会覆盖 */
        }
    }

    /// <summary>只关安装目录里的那一份，开发目录 / 别处的副本不动</summary>
    private static void StopProcesses(string dir)
    {
        foreach (var name in new[] { "MangaShelf", "MangaShelfServer" })
        {
            foreach (var p in Process.GetProcessesByName(name))
            {
                try
                {
                    var exe = p.MainModule?.FileName ?? "";
                    if (exe.Length > 0 && exe.StartsWith(dir, StringComparison.OrdinalIgnoreCase))
                    {
                        p.Kill(true);
                        p.WaitForExit(4000);
                    }
                }
                catch
                {
                    /* 关不掉就继续，删的时候再重试 */
                }
            }
        }
        Thread.Sleep(400);
    }

    private static void ClearRegistry(string dir)
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(Uninstaller.RegPath);
            var registered = key?.GetValue("InstallLocation") as string;
            // 只在登记的就是这个目录时才删，避免误删另一份安装
            if (registered is not null
                && !registered.TrimEnd('\\').Equals(dir.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
        }
        catch
        {
            /* 读不出来就按常规删 */
        }

        try { Registry.CurrentUser.DeleteSubKeyTree(Uninstaller.RegPath, false); }
        catch { /* 不存在就算了 */ }
    }

    private static void DeleteTree(string dir, bool keepData)
    {
        if (!Directory.Exists(dir)) return;

        if (!keepData)
        {
            if (TryDelete(dir, true)) return;
        }

        // 逐项删：保留 data 或部分文件被占用时，能删多少删多少
        foreach (var file in SafeGetFiles(dir))
        {
            TryDelete(file, false);
        }

        foreach (var sub in SafeGetDirs(dir))
        {
            if (keepData && Path.GetFileName(sub).Equals("data", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            TryDelete(sub, true);
        }

        // 全部删干净后，安装目录本身也可以去掉
        if (!keepData || !Directory.Exists(Path.Combine(dir, "data")))
        {
            TryDelete(dir, false);
        }
    }

    private static string[] SafeGetFiles(string dir)
    {
        try { return Directory.GetFiles(dir); } catch { return Array.Empty<string>(); }
    }

    private static string[] SafeGetDirs(string dir)
    {
        try { return Directory.GetDirectories(dir); } catch { return Array.Empty<string>(); }
    }

    /// <summary>
    /// 删除失败重试。exe 末尾释放得最慢：进程刚退出时文件映像还被系统/杀软占着，
    /// 而 Uninstall.exe 有 48MB，扫描一遍要好几秒，所以给它更长的耐心。
    /// </summary>
    private static bool TryDelete(string path, bool recursive)
    {
        var isExe = !recursive && path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase);
        var attempts = isExe ? 40 : 12;
        var delay = isExe ? 400 : 300;

        for (var i = 0; i < attempts; i++)
        {
            try
            {
                if (Directory.Exists(path))
                {
                    if (recursive) Directory.Delete(path, true);
                    else Directory.Delete(path, false);
                }
                else if (File.Exists(path))
                {
                    File.SetAttributes(path, FileAttributes.Normal);
                    File.Delete(path);
                }
                return true;
            }
            catch
            {
                if (i == attempts - 1) return false;
                Thread.Sleep(delay);
            }
        }
        return false;
    }
}

/// <summary>开始菜单文件夹、桌面快捷方式</summary>
internal static class Shortcuts
{
    private static string StartMenuDir => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        @"Microsoft\Windows\Start Menu\Programs", Uninstaller.AppName);

    public static void RemoveAll()
    {
        try
        {
            if (Directory.Exists(StartMenuDir)) Directory.Delete(StartMenuDir, true);
        }
        catch { /* ignore */ }

        try
        {
            var desktop = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                Uninstaller.AppName + ".lnk");
            if (File.Exists(desktop)) File.Delete(desktop);
        }
        catch { /* ignore */ }
    }
}
