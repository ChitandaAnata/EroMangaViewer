using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Principal;
using System.Threading;
using System.Windows.Forms;

namespace MangaShelf.Setup;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        Trace("main start, args=" + string.Join(" ", args));
        PrepareTemp();
        Trace("temp ready -> " + Path.GetTempPath());
        InitializeUi();
        Trace("ui init done");

        var silent = Has(args, "/S") || Has(args, "--silent");
        var dir = ArgValue(args, "/DIR=") ?? ArgValue(args, "--dir=") ?? ArgValue(args, "/D=");
        var desktop = !Has(args, "/NODESKTOP");

        try
        {
            if (silent)
            {
                Trace("silent mode, dir=" + (dir ?? "(default)"));
                Environment.ExitCode = RunSilent(dir, desktop) ? 0 : 1;
                Trace("silent done, exit=" + Environment.ExitCode);
                return;
            }

            Trace("gui mode");
            Application.Run(new SetupForm(dir));
        }
        finally
        {
            CleanupTemp();
        }
    }

    /// <summary>当前进程是否已经提权。装到 Program Files 这类位置必须靠它。</summary>
    internal static bool IsElevated
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

    /// <summary>
    /// 目标位置写不进去、而当前不是管理员时，用 UAC 把自己重启一次。
    /// 这就是标准安装包的行为：用户选 C:\Program Files 或 D:\ 根目录，弹一次权限确认，
    /// 确认后由提权后的进程继续装，而不是让用户「换个位置」。
    /// 返回 true 表示已经交给提权进程（调用方应该退出）。
    /// </summary>
    internal static bool TryElevate(string dir, bool silent, bool desktop)
    {
        // net48 没有 Environment.ProcessPath
        var exe = Process.GetCurrentProcess().MainModule?.FileName;
        if (string.IsNullOrWhiteSpace(exe)) return false;

        var psi = new ProcessStartInfo(exe) { UseShellExecute = true, Verb = "runas" };
        // net48 也没有 ArgumentList，手工拼命令行（路径带空格必须加引号）
        var argv = new List<string> { "--elevated", "/DIR=" + dir };
        if (silent) argv.Add("/S");
        if (!desktop) argv.Add("/NODESKTOP");
        psi.Arguments = string.Join(" ", argv.Select(QuoteArg));

        try
        {
            Trace("elevating for " + dir);
            using var child = Process.Start(psi);
            if (child is null) return false;

            if (silent)
            {
                child.WaitForExit();
                Environment.ExitCode = child.ExitCode;
                Trace("elevated child exit=" + child.ExitCode);
            }
            return true;
        }
        catch (Win32Exception ex) when (ex.NativeErrorCode == 1223)
        {
            Trace("elevation cancelled by user");
            return false;   // 用户在 UAC 对话框点了「否」
        }
        catch (Exception ex)
        {
            Trace("elevation failed: " + ex.Message);
            return false;
        }
    }

    /// <summary>
    /// 拼命令行时给含空格的参数加引号。net48 没有 ArgumentList，只能自己来。
    /// 注意用 IndexOf 而不是 Contains(char)：后者是 .NET Core 2.1 才有的重载。
    /// </summary>
    private static string QuoteArg(string value)
    {
        if (value.Length == 0) return "\"\"";
        if (value.IndexOf(' ') < 0 && value.IndexOf('\t') < 0 && value.IndexOf('"') < 0)
            return value;
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    /// <summary>
    /// 收掉自己在 exe 旁边建的 .setup-temp：
    /// 有些机器 %TEMP% 不可写，临时目录只能落在程序旁边，装完不该留着。
    /// </summary>
    private static void CleanupTemp()
    {
        try
        {
            var temp = Path.GetTempPath().TrimEnd('\\', '/');
            var exeDir = Path.GetDirectoryName(Application.ExecutablePath);
            if (string.IsNullOrEmpty(exeDir)) return;
            if (!temp.StartsWith(exeDir.TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase)) return;
            if (Directory.Exists(temp)) Directory.Delete(temp, true);
        }
        catch
        {
            /* 还被占用就留着，下次安装会覆盖 */
        }
    }

    /// <summary>排查用日志：默认不写，设 MANGASHELF_SETUP_TRACE=1 才落盘</summary>
    private static readonly bool TraceEnabled =
        Environment.GetEnvironmentVariable("MANGASHELF_SETUP_TRACE") == "1";

    internal static void Trace(string message)
    {
        if (!TraceEnabled) return;

        foreach (var dir in TraceDirs())
        {
            try
            {
                File.AppendAllText(
                    Path.Combine(dir, "ms-setup-trace.log"),
                    $"[{DateTime.Now:HH:mm:ss.fff}] {message}{Environment.NewLine}");
                return;
            }
            catch
            {
                /* 换下一个位置 */
            }
        }
    }

    private static IEnumerable<string> TraceDirs()
    {
        // exe 同目录最好找（用户就在那儿双击的）
        var exeDir = Path.GetDirectoryName(Application.ExecutablePath);
        if (!string.IsNullOrWhiteSpace(exeDir)) yield return exeDir;

        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (!string.IsNullOrWhiteSpace(local)) yield return local;

        var temp = Path.GetTempPath();
        if (!string.IsNullOrWhiteSpace(temp)) yield return temp;
    }

    /// <summary>
    /// 等价于 ApplicationConfiguration.Initialize()，但单步 try 住。
    /// EnableVisualStyles 会往临时目录写一个激活上下文文件，个别机器这一步会抛
    /// UnauthorizedAccessException 直接把进程带走；而我们的界面全是自绘控件，
    /// 没启用视觉样式也只是标准控件（比如对话框）朴素一点，远好过装不了。
    /// </summary>
    private static void InitializeUi()
    {
        try { Application.EnableVisualStyles(); } catch { /* 临时目录不给写就跳过 */ }
        try { Application.SetCompatibleTextRenderingDefault(false); } catch { /* ignore */ }
        // High DPI 由 app.manifest 里的 dpiAwareness 声明；net48 没有 SetHighDpiMode 这个 API
    }

    /// <summary>
    /// 把临时目录挪到一个确认可写的位置。
    /// 只设环境变量不够：得真写一个探针文件，再核对 Path.GetTempPath() 确实变了，
    /// 否则等于没设（有的环境 GetFolderPath 会返回空，拼出相对路径）。
    /// </summary>
    private static void PrepareTemp()
    {
        foreach (var dir in TempCandidates())
        {
            try
            {
                Directory.CreateDirectory(dir);

                var probe = Path.Combine(dir, "probe-" + Guid.NewGuid().ToString("N") + ".tmp");
                File.WriteAllText(probe, "ok");
                File.Delete(probe);

                Environment.SetEnvironmentVariable("TEMP", dir, EnvironmentVariableTarget.Process);
                Environment.SetEnvironmentVariable("TMP", dir, EnvironmentVariableTarget.Process);

                var now = Path.GetTempPath().TrimEnd('\\', '/');
                if (now.Equals(dir.TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase)) return;
            }
            catch
            {
                /* 换下一个候选目录 */
            }
        }
    }

    private static IEnumerable<string> TempCandidates()
    {
        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (!string.IsNullOrWhiteSpace(local))
        {
            yield return Path.Combine(local, "Temp", "MangaShelf-setup");
        }

        var exeDir = Path.GetDirectoryName(Application.ExecutablePath);
        if (!string.IsNullOrWhiteSpace(exeDir))
        {
            yield return Path.Combine(exeDir, ".setup-temp");
        }

        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (!string.IsNullOrWhiteSpace(profile))
        {
            yield return Path.Combine(profile, "MangaShelf-setup-temp");
        }
    }

    /// <summary>静默安装（给脚本和自动化用）：不弹窗、装完不启动</summary>
    private static bool RunSilent(string? dir, bool desktop)
    {
        var target = string.IsNullOrWhiteSpace(dir)
            ? Payload.ExistingDir() ?? Payload.DefaultDir()
            : dir!.Trim().Trim('"');

        try
        {
            Trace("target=" + target);
            Payload.CanInstallTo(target, out var probeReason, out var permissionDenied);
            Trace($"preflight permissionDenied={permissionDenied} reason={probeReason}");
            Payload.InstallAsync(target, (_, _) => { }).GetAwaiter().GetResult();
            Trace("extract done");
            Payload.CreateUninstaller(target);
            Trace("uninstaller linked");
            Payload.CreateShortcuts(target, desktop);
            Trace("shortcuts done");
            if (!desktop) Payload.RemoveDesktopShortcut();
            var registered = Payload.Register(target);
            Trace("registry done: " + registered);
            return true;
        }
        catch (UnauthorizedAccessException ex) when (!IsElevated && TryElevate(target, true, desktop))
        {
            // 真的没权限写：提权重来，和图形界面同一套处理
            Trace("access denied, handed over to elevated process: " + ex.Message);
            return true;
        }
        catch (Exception ex)
        {
            Trace("failed: " + ex);
            try
            {
                var log = Path.Combine(Path.GetTempPath(), "MangaShelf-setup.log");
                File.AppendAllText(log, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] 静默安装失败：{ex}{Environment.NewLine}");
            }
            catch
            {
                /* 日志写不进去也不影响退出码 */
            }
            return false;
        }
    }

    private static bool Has(string[] args, string flag) =>
        args.Any(a => a.Equals(flag, StringComparison.OrdinalIgnoreCase));

    private static string? ArgValue(string[] args, string prefix) =>
        args.FirstOrDefault(a => a.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            ?.Substring(prefix.Length)
            .Trim()
            .Trim('"');
}
