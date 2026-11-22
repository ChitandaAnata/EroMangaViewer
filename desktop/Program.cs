using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace MangaShelf;

internal static class Program
{
    private static Mutex? _instance;

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr FindWindow(string? className, string windowName);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hWnd, int cmd);

    [STAThread]
    private static void Main(string[] args)
    {
        // 卸载：安装目录里的 Uninstall.exe（与 MangaShelf.exe 是同一个文件），
        // 或者「应用和功能」里登记的 MangaShelf.exe --uninstall
        var exeName = Path.GetFileNameWithoutExtension(Application.ExecutablePath) ?? "";
        var looksLikeUninstaller =
            exeName.Equals("uninstall", StringComparison.OrdinalIgnoreCase) ||
            exeName.Equals("卸载", StringComparison.OrdinalIgnoreCase) ||
            exeName.StartsWith("unins", StringComparison.OrdinalIgnoreCase) ||
            exeName.StartsWith("ms-uninstall", StringComparison.OrdinalIgnoreCase);

        if (looksLikeUninstaller || args.Any(a => a.Equals("--uninstall", StringComparison.OrdinalIgnoreCase)))
        {
            // 卸载的临时目录不能放在安装目录里，否则「连数据一起删」会删掉自己
            Uninstaller.PrepareTemp();
            InitializeUi();
            Uninstaller.Run(args);
            return;
        }

        // 第一件事：把临时目录挪到程序自己的 data 下。
        // 有些机器 %TEMP% 不可写，不做这一步会在 Initialize 阶段直接崩。
        AppPaths.PrepareTemp();

        // 自检：只画一张窗口截图就退出，不启动后端（打包后核对界面用）
        var shot = args.FirstOrDefault(a => a.StartsWith("--shot=", StringComparison.OrdinalIgnoreCase));
        if (shot is not null)
        {
            InitializeUi();
            using var probe = new MainForm(captureOnly: true);
            probe.Show();
            Application.DoEvents();
            probe.PerformLayout();
            Application.DoEvents();
            Thread.Sleep(400);
            Application.DoEvents();
            var size = probe.ClientSize;
            using var bmp = new System.Drawing.Bitmap(size.Width, size.Height);
            probe.DrawToBitmap(bmp, new System.Drawing.Rectangle(0, 0, size.Width, size.Height));
            bmp.Save(shot.Substring("--shot=".Length), System.Drawing.Imaging.ImageFormat.Png);
            return;
        }

        // 单实例：同一份程序只开一个窗口；再从快捷方式点一次就把已有窗口叫到前台
        _instance = AcquireLock(out var isFirst);
        if (!isFirst)
        {
            Thread.Sleep(300);
            WakeWindow();
            return;
        }

        ApplicationConfiguration.Initialize();

        // 任何没被接住的异常都落进日志：窗口起不来时至少有据可查
        Application.ThreadException += (_, e) => AppPaths.Log("界面异常：" + e.Exception);
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
            AppPaths.Log("未处理异常：" + e.ExceptionObject);

        try
        {
            Application.Run(new MainForm());
        }
        catch (Exception ex)
        {
            AppPaths.Log("启动失败：" + ex);
            MessageBox.Show(
                "MangaShelf 启动失败：\n" + ex.Message + "\n\n详细信息见：\n" + AppPaths.LogFile,
                "MangaShelf", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        finally
        {
            try { _instance.ReleaseMutex(); } catch { }
        }
        GC.KeepAlive(_instance);
    }

    /// <summary>
    /// 等价于 ApplicationConfiguration.Initialize()，但每一步单独 try 住。
    /// EnableVisualStyles 会往临时目录写一个激活上下文文件，个别机器这一步会抛
    /// UnauthorizedAccessException 把进程直接带走；窗口里装的是 WebView2，
    /// 少这一下只是标准控件朴素一点，不该让程序打不开。
    /// </summary>
    private static void InitializeUi()
    {
        try { Application.EnableVisualStyles(); } catch { /* 写不了临时文件就跳过 */ }
        try { Application.SetCompatibleTextRenderingDefault(false); } catch { /* ignore */ }
        try { Application.SetHighDpiMode(HighDpiMode.PerMonitorV2); } catch { /* ignore */ }
    }

    private static Mutex AcquireLock(out bool isFirst)
    {
        var mutex = new Mutex(false, AppPaths.MutexName);
        try
        {
            try
            {
                isFirst = mutex.WaitOne(TimeSpan.Zero, false);
            }
            catch (AbandonedMutexException)
            {
                // 上一个进程被强杀，锁已经落到我们手上
                isFirst = true;
            }
        }
        catch
        {
            isFirst = true;
        }
        return mutex;
    }

    private static void WakeWindow()
    {
        try
        {
            var h = FindWindow(null, MainForm.WindowTitle);
            for (var i = 0; i < 40 && h == IntPtr.Zero; i++)
            {
                Thread.Sleep(150);
                h = FindWindow(null, MainForm.WindowTitle);
            }
            if (h != IntPtr.Zero)
            {
                ShowWindow(h, 9); // SW_RESTORE
                SetForegroundWindow(h);
            }
        }
        catch
        {
            /* ignore */
        }
    }
}
