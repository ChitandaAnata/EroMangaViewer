using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

namespace MangaShelf.Setup;

/// <summary>解压程序文件、建快捷方式、写「应用和功能」条目</summary>
internal static class Payload
{
    public const string AppName = "MangaShelf";
    public const string Version = "1.0.0";
    public const string ExeName = "MangaShelf.exe";
    public const string ServerName = "MangaShelfServer.exe";

    /// <summary>安装目录里的卸载程序：其实是 MangaShelf.exe 的硬链接，不额外占空间</summary>
    public const string UninstallName = "Uninstall.exe";

    private const string RegPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\MangaShelf";

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateHardLinkW(string newLink, string existingFile, IntPtr security);

    private static string? _defaultDir;

    /// <summary>
    /// 默认安装位置。优先 %LOCALAPPDATA%\Programs\MangaShelf（当前用户安装，不需要管理员），
    /// 但那层目录在个别机器上对未签名程序是拒绝写的（安全策略 / 受控文件夹访问），
    /// 所以逐个探一下，第一个真能写的才用。
    /// </summary>
    public static string DefaultDir()
    {
        if (_defaultDir is not null) return _defaultDir;

        foreach (var candidate in DefaultDirCandidates())
        {
            if (CanInstallTo(candidate, out _))
            {
                _defaultDir = candidate;
                return candidate;
            }
        }

        // 一个都不行：至少给个路径，让用户自己改
        _defaultDir = DefaultDirCandidates().First();
        return _defaultDir;
    }

    private static IEnumerable<string> DefaultDirCandidates()
    {
        // 已经以管理员身份运行时，默认就用标准位置 Program Files
        if (Program.IsElevated)
        {
            var pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            if (!string.IsNullOrWhiteSpace(pf))
            {
                yield return Path.Combine(pf, AppName);
            }
        }

        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (!string.IsNullOrWhiteSpace(local))
        {
            yield return Path.Combine(local, "Programs", AppName);
        }

        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (!string.IsNullOrWhiteSpace(profile))
        {
            yield return Path.Combine(profile, AppName);
        }

        var exeDir = Path.GetDirectoryName(Application.ExecutablePath);
        if (!string.IsNullOrWhiteSpace(exeDir))
        {
            yield return Path.Combine(exeDir, AppName);
        }
    }

    /// <summary>
    /// 装之前先按安装时的动作真试一遍：建目标目录、建一级子目录、写个文件。
    /// 比只写一个探针文件严格 —— 有些机器允许往现成目录里写文件，却不允许建目录，
    /// 只测写文件会误判成「可写」，装到一半才失败。
    ///
    /// 关键：判定只看「建目录 + 写文件」这几步。删探针属于收拾现场，
    /// 杀软、索引服务、云同步都可能短暂占住刚建好的目录让删除失败，
    /// 那种情况位置其实是能装的 —— 之前把它一起算进结果，导致
    /// 装到用户自己的目录里也弹出「需要管理员权限」。
    /// </summary>
    public static bool CanInstallTo(string dir, out string? reason)
        => CanInstallTo(dir, out reason, out _);

    /// <param name="permissionDenied">确实是权限不够（提权能解决），而不是路径不存在、被占用之类</param>
    public static bool CanInstallTo(string dir, out string? reason, out bool permissionDenied)
    {
        reason = null;
        permissionDenied = false;
        var createdRoot = false;
        var probeDir = Path.Combine(dir, "probe-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        string? stage = "创建安装目录";

        try
        {
            if (!Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
                createdRoot = true;
            }

            stage = "在安装目录里建子目录";
            Directory.CreateDirectory(probeDir);

            stage = "往安装目录写文件";
            File.WriteAllText(Path.Combine(probeDir, "probe.tmp"), "ok");

            // 能走到这里就说明位置可写，下面只是收拾现场，成败不影响结论
            CleanUpProbe(probeDir, createdRoot ? dir : null);
            return true;
        }
        catch (UnauthorizedAccessException ex)
        {
            permissionDenied = true;
            Program.Trace($"AccessDenied at [{stage}] hresult=0x{ex.HResult:X8} type={ex.GetType().Name} msg={ex.Message} inner={ex.InnerException?.Message} createdRoot={createdRoot} dir={dir}");
            reason = createdRoot
                // 连目标目录都建不出来 → 父级是受保护区域（Program Files、盘根…）
                ? $"没权限{stage}：{dir}。这个位置受系统保护，需要管理员权限才能装进去。"
                // 目录本来就在，只是写不进去 → 可能上次是管理员装的，或被杀软拦
                : $"没权限{stage}：{dir}。可以用管理员权限重试，或点「浏览…」换一个位置。";
        }
        catch (DirectoryNotFoundException)
        {
            reason = "找不到这个路径所在的位置，请检查盘符是否可用。";
        }
        catch (IOException ex)
        {
            // ERROR_ACCESS_DENIED 有时被包成 IOException，一样是权限问题
            permissionDenied = (ex.HResult & 0xFFFF) == 5;
            reason = $"{stage}时出错：{ex.Message}";
        }
        catch (Exception ex)
        {
            reason = $"{stage}时出错：{ex.Message}";
        }

        CleanUpProbe(probeDir, createdRoot ? dir : null);
        Program.Trace("CanInstallTo failed at [" + stage + "]: " + reason + " | dir=" + dir);
        return false;
    }

    /// <summary>删掉探针目录（尽力而为，带重试，失败就当没发生）</summary>
    private static void CleanUpProbe(string probeDir, string? rootToRemove)
    {
        for (var attempt = 0; attempt < 4; attempt++)
        {
            try
            {
                if (Directory.Exists(probeDir)) Directory.Delete(probeDir, true);
                if (rootToRemove is not null
                    && Directory.Exists(rootToRemove)
                    && Directory.GetFileSystemEntries(rootToRemove).Length == 0)
                {
                    Directory.Delete(rootToRemove);
                }

                return;
            }
            catch (Exception ex)
            {
                Program.Trace($"probe cleanup attempt {attempt + 1} failed: {ex.Message}");
                Thread.Sleep(60 * (attempt + 1));
            }
        }

        // 真删不掉也就是留个空目录，不影响安装，下次装会覆盖
    }

    /// <summary>把异常翻译成用户能照着做的一句话</summary>
    public static string Explain(Exception ex)
    {
        if (ex is UnauthorizedAccessException)
        {
            return "安装失败：目标位置写不进去（可能被安全软件或系统策略拦住）。请换一个位置后重试。";
        }

        if (ex is IOException io && (io.HResult & 0xFFFF) is 32 or 33)
        {
            return "安装失败：目标目录里有文件正被占用，先关掉 MangaShelf 再重试。";
        }

        if (ex is IOException io2 && (io2.HResult & 0xFFFF) == 112)
        {
            return "安装失败：磁盘空间不足。";
        }

        return "安装失败：" + ex.Message;
    }

    private static long? _total;

    /// <summary>解压后要占多少字节（显示体积、检查磁盘空间）</summary>
    public static long TotalBytes
    {
        get
        {
            if (_total is { } known) return known;
            using var zip = OpenZip();
            _total = zip.Entries.Where(e => !IsDir(e)).Sum(e => e.Length);
            return _total.Value;
        }
    }

    public static string FormatSize(long bytes) =>
        bytes >= 1024L * 1024 * 1024
            ? (bytes / 1024d / 1024 / 1024).ToString("0.0") + " GB"
            : (bytes / 1024d / 1024).ToString("0") + " MB";

    private static ZipArchive OpenZip()
    {
        var asm = System.Reflection.Assembly.GetExecutingAssembly();
        var stream = asm.GetManifestResourceStream("payload.zip")
                     ?? throw new InvalidOperationException("安装包里没有找到程序文件（payload.zip）。");
        return new ZipArchive(stream, ZipArchiveMode.Read);
    }

    private static bool IsDir(ZipArchiveEntry e) =>
        e.FullName.EndsWith("/", StringComparison.Ordinal) || e.FullName.EndsWith("\\", StringComparison.Ordinal);

    /// <summary>之前装过就返回安装目录（用于把路径框预填好）</summary>
    public static string? ExistingDir()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RegPath);
            var dir = key?.GetValue("InstallLocation") as string;
            if (!string.IsNullOrWhiteSpace(dir) && File.Exists(Path.Combine(dir, ExeName))) return dir;
        }
        catch
        {
            /* ignore */
        }
        return null;
    }

    public static bool HasEnoughSpace(string dir)
    {
        try
        {
            var root = Path.GetPathRoot(Path.GetFullPath(dir));
            if (string.IsNullOrEmpty(root)) return true;
            var drive = new DriveInfo(root);
            return drive.AvailableFreeSpace > TotalBytes + 64L * 1024 * 1024;
        }
        catch
        {
            return true;
        }
    }

    /// <summary>
    /// 覆盖安装时先清掉旧的程序目录，但绝不动 data —— 里面是漫画库设置、数据库和缩略图缓存。
    /// </summary>
    private static void CleanOldFiles(string dir)
    {
        foreach (var name in new[] { "backend", "web" })
        {
            var path = Path.Combine(dir, name);
            try
            {
                if (Directory.Exists(path)) Directory.Delete(path, true);
            }
            catch
            {
                /* 删不掉就让解压覆盖 */
            }
        }

        foreach (var name in new[] { "MangaShelf.exe", "Uninstall.exe", "WebView2Loader.dll" })
        {
            var path = Path.Combine(dir, name);
            try
            {
                if (File.Exists(path)) File.Delete(path);
            }
            catch
            {
                /* 让解压覆盖 */
            }
        }
    }

    /// <summary>关掉还开着的 MangaShelf 与它拉起的 Python 后端，否则文件被占用</summary>
    public static void StopRunning(string dir)
    {
        foreach (var name in new[] { "MangaShelf", "MangaShelfServer" })
        {
            foreach (var p in Process.GetProcessesByName(name))
            {
                try
                {
                    // 只关这个目录里的那份；别处的副本（比如开发目录）不动
                    var exe = p.MainModule?.FileName ?? "";
                    if (exe.Length == 0 || exe.StartsWith(dir, StringComparison.OrdinalIgnoreCase))
                    {
                        // net48 的 Kill 没有「连子进程一起」的重载
                        p.Kill();
                        p.WaitForExit(4000);
                    }
                }
                catch
                {
                    /* 关不掉就继续，解压时再说 */
                }
            }
        }
        Thread.Sleep(400);
    }

    public static async Task InstallAsync(
        string dir,
        Action<int, string> report,
        CancellationToken ct = default)
    {
        await Task.Run(() =>
        {
            Directory.CreateDirectory(dir);
            StopRunning(dir);
            CleanOldFiles(dir);

            using var zip = OpenZip();
            var total = Math.Max(1L, zip.Entries.Where(e => !IsDir(e)).Sum(e => e.Length));
            long done = 0;
            var lastReport = -1;
            var lastTick = 0;

            foreach (var entry in zip.Entries)
            {
                ct.ThrowIfCancellationRequested();
                var relative = entry.FullName.Replace('\\', '/').TrimStart('/');
                if (relative.Length == 0) continue;
                var target = Path.Combine(dir, relative.Replace('/', Path.DirectorySeparatorChar));

                if (IsDir(entry))
                {
                    Directory.CreateDirectory(target);
                    continue;
                }

                var parent = Path.GetDirectoryName(target);
                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);

                using var src = entry.Open();
                using var dst = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16);
                var buffer = new byte[1 << 16];
                int read;
                while ((read = src.Read(buffer, 0, buffer.Length)) > 0)
                {
                    ct.ThrowIfCancellationRequested();
                    dst.Write(buffer, 0, read);
                    done += read;

                    var pct = (int)(done * 100 / total);
                    var now = Environment.TickCount;
                    if (pct != lastReport && (now - lastTick > 60 || pct == 100))
                    {
                        lastTick = now;
                        lastReport = pct;
                        report(pct, FriendlyName(relative));
                    }
                }
            }

            report(100, "正在创建快捷方式…");
        }, ct);
    }

    /// <summary>进度条上只说清正在装哪一类，不刷长路径</summary>
    private static string FriendlyName(string relative)
    {
        var parts = relative.Replace('\\', '/').Split('/');
        // net48 没有 System.Index，用不了 parts[^1]
        var last = parts[parts.Length - 1];
        return parts[0] switch
        {
            "backend" => parts.Length > 1 ? "程序组件 · " + last : "程序组件",
            "web" => "界面文件 · " + last,
            "assets" => "图标资源",
            _ => last,
        };
    }

    /// <summary>
    /// 在安装目录里放一个 Uninstall.exe。用硬链接指向 MangaShelf.exe：
    /// 同一个文件两个名字，不重复占磁盘，双击它就会走卸载流程。
    /// </summary>
    public static void CreateUninstaller(string dir)
    {
        var exe = Path.Combine(dir, ExeName);
        var link = Path.Combine(dir, UninstallName);
        try
        {
            if (File.Exists(link)) File.Delete(link);
        }
        catch
        {
            /* 删不掉就让下面覆盖 */
        }
        try
        {
            if (CreateHardLinkW(link, exe, IntPtr.Zero)) return;
        }
        catch
        {
            /* 不是 NTFS 就退回复制 */
        }
        try
        {
            File.Copy(exe, link, true);
        }
        catch
        {
            /* 建不出来也不影响主程序，只是少一个入口 */
        }
    }

    /// <summary>
    /// 桌面 + 开始菜单快捷方式。
    /// 用系统的 IShellLink 写 .lnk（不依赖 WScript.Shell —— 不少安全策略会把脚本宿主
    /// COM 直接禁掉，那样快捷方式就静默失败了）。
    /// 返回是否至少建成了开始菜单里的主快捷方式。
    /// </summary>
    public static bool CreateShortcuts(string dir, bool desktop)
    {
        var exe = Path.Combine(dir, ExeName);
        var uninstall = Path.Combine(dir, UninstallName);
        if (!File.Exists(uninstall)) uninstall = exe;

        var startMenu = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            @"Microsoft\Windows\Start Menu\Programs", AppName);

        var ok = false;
        try
        {
            Directory.CreateDirectory(startMenu);
            Program.Trace("shortcut dir ok: " + startMenu);
            ok = MakeShortcut(Path.Combine(startMenu, AppName + ".lnk"), exe, dir, $"{AppName} —— 本地漫画书架");
            Program.Trace("shortcut main: " + ok);
            var un = MakeShortcut(Path.Combine(startMenu, "卸载 " + AppName + ".lnk"), uninstall, dir, $"卸载 {AppName}");
            Program.Trace("shortcut uninstall: " + un);
        }
        catch (Exception ex)
        {
            Program.Trace("shortcut failed: " + ex.GetType().Name + " " + ex.Message);
        }

        if (!desktop) return ok;
        try
        {
            var link = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                AppName + ".lnk");
            MakeShortcut(link, exe, dir, $"{AppName} —— 本地漫画书架");
        }
        catch
        {
            /* ignore */
        }
        return ok;
    }

    public static void RemoveDesktopShortcut()
    {
        try
        {
            var link = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                AppName + ".lnk");
            if (File.Exists(link)) File.Delete(link);
        }
        catch
        {
            /* ignore */
        }
    }

    private static bool MakeShortcut(string linkPath, string target, string workingDir, string description)
    {
        if (WriteShortcut(linkPath, target, workingDir, description)) return true;
        return WriteShortcutViaScriptHost(linkPath, target, workingDir, description);
    }

    /// <summary>首选：系统内置的 IShellLink，不碰脚本宿主</summary>
    private static bool WriteShortcut(string linkPath, string target, string workingDir, string description)
    {
        object? com = null;
        try
        {
            com = new ShellLinkCoClass();
            var link = (IShellLinkW)com;
            link.SetPath(target);
            link.SetWorkingDirectory(workingDir);
            link.SetDescription(description);
            link.SetIconLocation(target, 0);
            ((IPersistFile)link).Save(linkPath, true);
            return File.Exists(linkPath);
        }
        catch (Exception ex)
        {
            Program.Trace("shortcut(com) failed: " + ex.GetType().Name + " " + ex.Message);
            return false;
        }
        finally
        {
            if (com is not null)
            {
                try { Marshal.FinalReleaseComObject(com); } catch { /* ignore */ }
            }
        }
    }

    /// <summary>兜底：WScript.Shell（有的环境只允许这一条路）</summary>
    private static bool WriteShortcutViaScriptHost(string linkPath, string target, string workingDir, string description)
    {
        try
        {
            var shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType is null) return false;
            dynamic shell = Activator.CreateInstance(shellType)!;
            dynamic link = shell.CreateShortcut(linkPath);
            link.TargetPath = target;
            link.Arguments = "";
            link.WorkingDirectory = workingDir;
            link.IconLocation = target + ",0";
            link.Description = description;
            link.Save();
            Marshal.FinalReleaseComObject(link);
            Marshal.FinalReleaseComObject(shell);
            return File.Exists(linkPath);
        }
        catch (Exception ex)
        {
            Program.Trace("shortcut(wsh) failed: " + ex.GetType().Name + " " + ex.Message);
            return false;
        }
    }

    [ComImport]
    [Guid("00021401-0000-0000-C000-000000000046")]
    private class ShellLinkCoClass
    {
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("000214F9-0000-0000-C000-000000000046")]
    private interface IShellLinkW
    {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, uint fFlags);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        void GetHotkey(out short pwHotkey);
        void SetHotkey(short wHotkey);
        void GetShowCmd(out int piShowCmd);
        void SetShowCmd(int iShowCmd);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int piIcon);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
        void Resolve(IntPtr hwnd, uint fFlags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("0000010b-0000-0000-C000-000000000046")]
    private interface IPersistFile
    {
        void GetClassID(out Guid pClassID);
        [PreserveSig]
        int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, [MarshalAs(UnmanagedType.Bool)] bool fRemember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
        void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
    }

    /// <summary>
    /// 写「设置 → 应用 → 已安装的应用」里的条目。
    /// 有的环境会把 Uninstall 注册表键锁住（组策略 / 安全软件），
    /// 那种情况下安装照样能用，只是不在列表里显示，所以返回 false 而不是抛出去。
    /// </summary>
    public static bool Register(string dir)
    {
        try
        {
            var exe = Path.Combine(dir, ExeName);
            var uninstall = Path.Combine(dir, UninstallName);
            if (!File.Exists(uninstall)) uninstall = exe;

            using var key = Registry.CurrentUser.CreateSubKey(RegPath, true);
            key.SetValue("DisplayName", AppName);
            key.SetValue("DisplayVersion", Version);
            key.SetValue("Publisher", AppName);
            key.SetValue("InstallLocation", dir);
            key.SetValue("DisplayIcon", exe + ",0");
            key.SetValue("UninstallString", $"\"{uninstall}\"");
            key.SetValue("QuietUninstallString", $"\"{uninstall}\" /S");
            key.SetValue("NoModify", 1, RegistryValueKind.DWord);
            key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            key.SetValue("EstimatedSize", (int)(TotalBytes / 1024), RegistryValueKind.DWord);
            try
            {
                key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"), RegistryValueKind.String);
            }
            catch
            {
                /* 日期不是必需的 */
            }
            return true;
        }
        catch
        {
            return false;
        }
    }

    public static void Launch(string dir)
    {
        try
        {
            Process.Start(new ProcessStartInfo(Path.Combine(dir, ExeName))
            {
                WorkingDirectory = dir,
                UseShellExecute = true,
            });
        }
        catch
        {
            /* ignore */
        }
    }
}
