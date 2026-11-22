using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace MangaShelf;

/// <summary>
/// 目录定位。程序文件和 backend、web 在同一层；数据目录优先放在程序目录的 data 里，
/// 程序目录不可写时（典型是装在 C:\Program Files）自动落到 %LOCALAPPDATA%\MangaShelf。
/// </summary>
internal static class AppPaths
{
    private static string? _root;
    private static string? _data;

    public static string Root()
    {
        if (_root is not null) return _root;

        // 开发模式：外壳就放在源码目录里（或者 dev.bat 设了 MANGASHELF_HOME），
        // 那就用当前源码的 web/ 和 server/，不用先组装到 dist/。
        var home = Environment.GetEnvironmentVariable("MANGASHELF_HOME");
        if (!string.IsNullOrWhiteSpace(home) && IsSourceRoot(home))
        {
            _root = Path.GetFullPath(home).TrimEnd('\\');
            return _root;
        }

        // 开发模式（外置）：外壳被放在 %LOCALAPPDATA%\MangaShelfDev 之类**不带低完整性
        // 标签**的目录里跑，源码仍在工作区。此时同目录下有个 dev-home.txt 指向源码根。
        // 为什么要挪出工作区：工作区目录被打了 Low 完整性标签，里面的 exe 继承该标签，
        // 那样进程虽然能读盘，却驱不动资源管理器（打开文件夹会失败 / explorer 崩 0xC0000142）。
        var homeFile = Path.Combine(AppContext.BaseDirectory, "dev-home.txt");
        try
        {
            if (File.Exists(homeFile))
            {
                var target = File.ReadAllText(homeFile).Trim().Trim('"');
                if (IsSourceRoot(target))
                {
                    _root = Path.GetFullPath(target).TrimEnd('\\');
                    return _root;
                }
            }
        }
        catch
        {
            /* 读不到就当没有，继续按常规探测 */
        }

        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (var i = 0; i < 6 && dir is not null; i++)
        {
            // 装好的版本：backend/ + web/ 同层
            if (Directory.Exists(Path.Combine(dir.FullName, "backend"))
                && Directory.Exists(Path.Combine(dir.FullName, "web")))
            {
                _root = dir.FullName;
                return _root;
            }
            // 从 desktop\bin\... 往上跑时，认源码目录（app.py + server + web）
            if (IsSourceRoot(dir.FullName))
            {
                _root = dir.FullName;
                return _root;
            }
            dir = dir.Parent;
        }

        // 找不到就退回 exe 自己那一层，至少不让程序直接崩
        _root = AppContext.BaseDirectory.TrimEnd('\\');
        return _root;
    }

    /// <summary>
    /// 这个目录是不是源码根目录：有 app.py、server\ 和 web\，但没有打包好的 backend\。
    /// 用来区分「从源码跑的开发版」和「装好 / 组装好的成品」。
    /// </summary>
    public static bool IsSourceRoot(string dir)
    {
        if (string.IsNullOrWhiteSpace(dir)) return false;
        return File.Exists(Path.Combine(dir, "app.py"))
            && Directory.Exists(Path.Combine(dir, "server"))
            && Directory.Exists(Path.Combine(dir, "web"));
    }

    /// <summary>
    /// 数据目录。顺序：MANGASHELF_DATA 覆盖 → 程序目录\data → %LOCALAPPDATA%\MangaShelf。
    /// 装到 Program Files 时程序目录对普通用户只读，必须回退到用户目录，
    /// 否则数据库、封面缓存、WebView2 用户数据全都写不进去，程序直接起不来。
    /// </summary>
    public static string DataDir
    {
        get
        {
            if (_data is not null) return _data;
            _data = ResolveDataDir();
            return _data;
        }
    }

    private static string ResolveDataDir()
    {
        foreach (var candidate in DataCandidates())
        {
            if (!CanWrite(candidate)) continue;
            _dataWritable = true;
            return Path.GetFullPath(candidate).TrimEnd('\\');
        }

        // 全都不行：还是给程序目录，让错误提示能指向一个具体路径
        return Path.Combine(Root(), "data");
    }

    private static bool _dataWritable;

    /// <summary>数据目录是否真的可写。false 表示启动前就该拦住，别去拉后端空等。</summary>
    public static bool DataDirWritable
    {
        get
        {
            _ = DataDir;   // 触发一次解析，顺便把结果落在 _dataWritable
            return _dataWritable;
        }
    }

    /// <summary>
    /// 数据目录候选，按优先级排。装到 Program Files 时程序目录只读，必须落用户目录；
    /// 而用户目录在个别机器上也会因为组策略、漫游配置、安全软件而写不进去，
    /// 所以备一串位置逐个实测，别吊死在一个路径上。
    /// </summary>
    private static IEnumerable<string> DataCandidates()
    {
        var custom = Environment.GetEnvironmentVariable("MANGASHELF_DATA");
        if (!string.IsNullOrWhiteSpace(custom)) yield return custom;

        yield return Path.Combine(Root(), "data");

        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (!string.IsNullOrWhiteSpace(local))
        {
            yield return Path.Combine(local, "MangaShelf");
            yield return Path.Combine(local, "Programs", "MangaShelf", "data");
        }

        var roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        if (!string.IsNullOrWhiteSpace(roaming)) yield return Path.Combine(roaming, "MangaShelf");

        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (!string.IsNullOrWhiteSpace(profile)) yield return Path.Combine(profile, "MangaShelf");

        var common = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
        if (!string.IsNullOrWhiteSpace(common)) yield return Path.Combine(common, "MangaShelf");

        // 临时目录也会被清，但总好过程序直接起不来
        yield return Path.Combine(Path.GetTempPath(), "MangaShelf");
    }

    private static bool CanWrite(string dir)
    {
        try
        {
            Directory.CreateDirectory(dir);
            var probe = Path.Combine(dir, "probe-" + Guid.NewGuid().ToString("N") + ".tmp");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);
            return true;
        }
        catch
        {
            return false;
        }
    }

    public static string LogFile => Path.Combine(DataDir, "startup.log");
    public static string WindowFile => Path.Combine(DataDir, "window.json");
    public static string WebViewDir => Path.Combine(DataDir, "webview2");
    public static string TempDir => Path.Combine(DataDir, "temp");
    public static string BackendExe => Path.Combine(Root(), "backend", "MangaShelfServer.exe");

    /// <summary>
    /// 把本进程的临时目录挪到一个确认可写的位置。
    /// 有些机器的 %TEMP% 对普通用户不可写（组策略 / 权限被改过 / 短名路径），
    /// .NET 启用视觉样式、WebView2、PyInstaller 都会往临时目录写文件，
    /// 不挪的话程序在 Initialize 阶段就崩。
    ///
    /// 只设环境变量不够：要真写一个探针文件，并核对 Path.GetTempPath() 确实变了，
    /// 否则等于没设（有的环境 GetFolderPath 会返回空字符串，拼出相对路径）。
    /// </summary>
    public static void PrepareTemp()
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
                /* 换下一个候选 */
            }
        }
    }

    private static IEnumerable<string> TempCandidates()
    {
        // 首选程序自己的 data\temp：跟着安装目录走，卸载时一起清掉
        yield return TempDir;

        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (!string.IsNullOrWhiteSpace(local))
        {
            yield return Path.Combine(local, "Temp", "MangaShelf");
        }

        var exeDir = Path.GetDirectoryName(Environment.ProcessPath ?? AppContext.BaseDirectory);
        if (!string.IsNullOrWhiteSpace(exeDir))
        {
            yield return Path.Combine(exeDir, ".temp");
        }
    }

    /// <summary>单实例锁名带目录摘要：装在别处的另一份程序各开各的窗口</summary>
    public static string MutexName
    {
        get
        {
            var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(Root().TrimEnd('\\').ToLowerInvariant()));
            return "MangaShelf.instance." + Convert.ToHexString(bytes, 0, 6).ToLowerInvariant();
        }
    }

    public static void EnsureDataDir()
    {
        Directory.CreateDirectory(DataDir);
    }

    public static void Log(string line)
    {
        var text = $"[{DateTime.Now:HH:mm:ss}] {line}{Environment.NewLine}";
        foreach (var path in LogPaths())
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                File.AppendAllText(path, text, new UTF8Encoding(true));
                return;
            }
            catch
            {
                /* 换下一个位置 */
            }
        }
    }

    /// <summary>日志主位置在数据目录；数据目录写不进去时退到已确认可写的临时目录</summary>
    private static IEnumerable<string> LogPaths()
    {
        yield return LogFile;

        var temp = Path.Combine(Path.GetTempPath(), "MangaShelf-startup.log");
        yield return temp;
    }

    /// <summary>用户能去哪里找日志：返回第一条真能写的路径</summary>
    public static string LogLocation()
    {
        foreach (var path in LogPaths())
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                return path;
            }
            catch
            {
                /* 继续找 */
            }
        }
        return LogFile;
    }

    public static void ClearLog()
    {
        try
        {
            EnsureDataDir();
            File.WriteAllText(LogFile, "", new UTF8Encoding(true));
        }
        catch { }
    }
}
