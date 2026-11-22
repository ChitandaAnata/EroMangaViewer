using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace MangaShelf;

/// <summary>
/// 原生窗口外壳。窗口里放的是 WebView2，加载的地址就是本机后端托管的网页端
/// —— 同一份 HTML / CSS / JS，所以界面、交互、功能与浏览器里完全一致。
/// 外壳只多做四件事：拉后端、定窗口、管外链、退出时收尾。
/// </summary>
public sealed class MainForm : Form
{
    public const string WindowTitle = "MangaShelf";

    private static readonly Color BgColor = Color.FromArgb(11, 13, 18);

    private readonly WebView2 _view = new();
    private readonly Panel _overlay = new();
    private readonly Label _overlayTitle = new();
    private readonly Label _overlayHint = new();
    private readonly Button _retryBtn = new();

    private Backend? _backend;
    private readonly bool _captureOnly;

    public MainForm(bool captureOnly = false)
    {
        _captureOnly = captureOnly;

        Text = WindowTitle;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = BgColor;
        ClientSize = new Size(1360, 860);
        DoubleBuffered = true;
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

        _view.Dock = DockStyle.Fill;
        Controls.Add(_view);

        BuildOverlay();

        if (!_captureOnly)
        {
            RestoreWindowBox();
            Load += async (_, _) => await BootAsync();
            FormClosing += OnFormClosing;
            KeyPreview = true;
            KeyDown += OnKeyDown;
        }
    }

    // ---------------------------------------------------------------- 启动

    private void BuildOverlay()
    {
        _overlay.Dock = DockStyle.Fill;
        _overlay.BackColor = BgColor;

        _overlayTitle.Text = "MangaShelf";
        _overlayTitle.ForeColor = Color.FromArgb(240, 242, 248);
        _overlayTitle.Font = new Font("Segoe UI", 26F, FontStyle.Bold);
        _overlayTitle.AutoSize = true;

        _overlayHint.Text = "正在启动本地服务…";
        _overlayHint.ForeColor = Color.FromArgb(140, 148, 165);
        _overlayHint.Font = new Font("Microsoft YaHei UI", 10.5F);
        _overlayHint.AutoSize = true;
        _overlayHint.MaximumSize = new Size(560, 0);
        _overlayHint.TextAlign = ContentAlignment.MiddleCenter;

        _retryBtn.Text = "重试";
        _retryBtn.Visible = false;
        _retryBtn.AutoSize = false;
        _retryBtn.Size = new Size(96, 34);
        _retryBtn.FlatStyle = FlatStyle.Flat;
        _retryBtn.FlatAppearance.BorderSize = 0;
        _retryBtn.BackColor = Color.FromArgb(255, 95, 126);
        _retryBtn.ForeColor = Color.White;
        _retryBtn.Font = new Font("Microsoft YaHei UI", 10F);
        _retryBtn.Cursor = Cursors.Hand;
        _retryBtn.Click += async (_, _) => await BootAsync();

        _overlay.Controls.Add(_overlayTitle);
        _overlay.Controls.Add(_overlayHint);
        _overlay.Controls.Add(_retryBtn);
        Controls.Add(_overlay);
        _overlay.BringToFront();

        _overlay.Resize += (_, _) => LayoutOverlay();
        LayoutOverlay();
    }

    private void LayoutOverlay()
    {
        var cx = _overlay.ClientSize.Width / 2;
        var cy = _overlay.ClientSize.Height / 2;
        _overlayTitle.Location = new Point(cx - _overlayTitle.Width / 2, cy - 64);
        _overlayHint.Location = new Point(cx - _overlayHint.Width / 2, cy + 4);
        _retryBtn.Location = new Point(cx - _retryBtn.Width / 2, _overlayHint.Bottom + 18);
    }

    private void SetOverlay(string title, string hint, bool showRetry)
    {
        _overlayTitle.Text = title;
        _overlayHint.Text = hint;
        _retryBtn.Visible = showRetry;
        _overlay.Visible = true;
        _overlay.BringToFront();
        LayoutOverlay();
    }

    private async Task BootAsync()
    {
        if (_backend is not null) return; // 已经在启动中

        _retryBtn.Visible = false;
        _overlayTitle.Text = "MangaShelf";
        _overlayHint.Text = "正在启动本地服务…";
        LayoutOverlay();

        AppPaths.ClearLog();
        AppPaths.Log("启动 " + Application.ExecutablePath);
        AppPaths.Log("程序目录 " + AppPaths.Root());
        AppPaths.Log("数据目录 " + AppPaths.DataDir);

        // 一个能写的位置都没有就直接说清楚，别去拉后端空等几十秒
        if (!AppPaths.DataDirWritable)
        {
            SetOverlay("没有可写的数据目录",
                "程序需要可写目录来放数据库和封面缓存，但下面这些位置都写不进去：\n"
                + "  · " + AppPaths.DataDir + "\n"
                + "请检查安全软件 / 文件夹权限，或把程序解压到你有写权限的目录再运行。",
                false);
            return;
        }

        var backend = new Backend(AppPaths.Root());
        _backend = backend;
        var ok = await backend.StartAsync(AppPaths.Log);
        if (!ok)
        {
            var detail = backend.LastError;
            _backend = null;
            backend.Dispose();

            var hint = string.IsNullOrWhiteSpace(detail)
                ? "本地服务没能启动。"
                : "本地服务没能启动：" + detail;
            hint += "\n数据目录：" + AppPaths.DataDir
                 + "\n日志：" + AppPaths.LogLocation();
            SetOverlay("启动失败", hint, true);
            return;
        }

        await InitWebViewAsync(backend.BaseUrl);
    }

    private async Task InitWebViewAsync(string baseUrl)
    {
        try
        {
            Directory.CreateDirectory(AppPaths.WebViewDir);
            var env = await CoreWebView2Environment.CreateAsync(null, AppPaths.WebViewDir);
            await _view.EnsureCoreWebView2Async(env);
            // 必须在控件初始化之后才能设：页面加载前用同色底，避免闪一下白屏
            _view.DefaultBackgroundColor = BgColor;

            var core = _view.CoreWebView2;

            // 站内跳转留在应用里，站外链接交给系统浏览器
            core.NewWindowRequested += (_, e) =>
            {
                if (e.Uri.StartsWith(baseUrl, StringComparison.OrdinalIgnoreCase)) return;
                e.Handled = true;
                OpenExternal(e.Uri);
            };
            core.NavigationStarting += (_, e) =>
            {
                if (e.Uri.StartsWith(baseUrl, StringComparison.OrdinalIgnoreCase)) return;
                if (e.Uri.StartsWith("about:", StringComparison.OrdinalIgnoreCase)) return;
                if (e.Uri.StartsWith("data:", StringComparison.OrdinalIgnoreCase)) return;
                if (e.Uri.StartsWith("blob:", StringComparison.OrdinalIgnoreCase)) return;
                e.Cancel = true;
                OpenExternal(e.Uri);
            };
            core.ProcessFailed += (_, e) => AppPaths.Log("渲染进程异常：" + e.ProcessFailedKind);

            // 页面真正画出来了才撤掉启动遮罩，否则会看到一闪而过的白底
            core.NavigationCompleted += (_, e) =>
            {
                if (!e.IsSuccess)
                {
                    AppPaths.Log("页面加载失败：" + e.WebErrorStatus);
                    SetOverlay("页面加载失败", "错误：" + e.WebErrorStatus, true);
                    return;
                }
                _overlay.Visible = false;
                _view.Focus();
            };

            _view.Source = new Uri(baseUrl);
            AppPaths.Log("界面就绪 " + baseUrl);
        }
        catch (Exception ex)
        {
            AppPaths.Log("界面初始化失败：" + ex);
            var msg = ex.Message.Contains("WebView2", StringComparison.OrdinalIgnoreCase)
                ? "没有找到 WebView2 运行时。装一下 Microsoft Edge WebView2 Runtime 再打开。"
                : "界面初始化失败：" + ex.Message;
            SetOverlay("无法显示界面", msg, true);
        }
    }

    private void OnKeyDown(object? sender, KeyEventArgs e)
    {
        // 调试用：F12 打开内核开发者工具，F5 刷新（和浏览器一致）
        if (e.KeyCode == Keys.F12 && _view.CoreWebView2 is not null)
        {
            _view.CoreWebView2.OpenDevToolsWindow();
            e.Handled = true;
        }
        else if (e.KeyCode == Keys.F5 && _view.CoreWebView2 is not null)
        {
            _view.CoreWebView2.Reload();
            e.Handled = true;
        }
    }

    private static void OpenExternal(string url)
    {
        try
        {
            if (Uri.TryCreate(url, UriKind.Absolute, out var uri)
                && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps))
            {
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            }
        }
        catch (Exception ex)
        {
            AppPaths.Log("打开外链失败：" + ex.Message);
        }
    }

    // ---------------------------------------------------------------- 窗口状态

    private void RestoreWindowBox()
    {
        try
        {
            if (!File.Exists(AppPaths.WindowFile)) return;
            using var doc = JsonDocument.Parse(File.ReadAllText(AppPaths.WindowFile));
            var r = doc.RootElement;
            var w = r.TryGetProperty("w", out var we) ? we.GetInt32() : 0;
            var h = r.TryGetProperty("h", out var he) ? he.GetInt32() : 0;
            var x = r.TryGetProperty("x", out var xe) ? xe.GetInt32() : 0;
            var y = r.TryGetProperty("y", out var ye) ? ye.GetInt32() : 0;
            if (w < 900 || h < 600) return;

            var screen = Screen.FromPoint(new Point(x + w / 2, y + h / 2)).WorkingArea;
            if (x < screen.Left - 40 || y < screen.Top - 40 || x > screen.Right - 200 || y > screen.Bottom - 120)
            {
                return; // 记下来的位置已经不在任何屏幕上（拔了外接屏），保持居中
            }
            StartPosition = FormStartPosition.Manual;
            Bounds = new Rectangle(x, y, w, h);
            if (r.TryGetProperty("max", out var me) && me.GetBoolean())
            {
                WindowState = FormWindowState.Maximized;
            }
        }
        catch
        {
            /* 记不下来就用默认尺寸 */
        }
    }

    private void SaveWindowBox()
    {
        try
        {
            var max = WindowState == FormWindowState.Maximized;
            var b = max ? RestoreBounds : Bounds;
            AppPaths.EnsureDataDir();
            var json = JsonSerializer.Serialize(new
            {
                x = b.X,
                y = b.Y,
                w = b.Width,
                h = b.Height,
                max,
            });
            File.WriteAllText(AppPaths.WindowFile, json);
        }
        catch
        {
            /* ignore */
        }
    }

    private void OnFormClosing(object? sender, FormClosingEventArgs e)
    {
        SaveWindowBox();
        _backend?.Dispose();
        _backend = null;
    }
}
