using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using MangaShelf.UI;

namespace MangaShelf.Setup;

/// <summary>安装向导：选目录 → 解压 → 建快捷方式 → 写卸载项，一个窗口三段状态</summary>
internal sealed class SetupForm : Form
{
    private enum Step { Ready, Installing, Done, Failed }

    private const int W = 620;
    private const int H = 432;

    private readonly FlatInput _path = new();
    private readonly FlatButton _browse = new() { Primary = false, Text = "浏览…" };
    private readonly FlatCheck _desktop = new() { Text = "创建桌面快捷方式", Checked = true };
    private readonly FlatCheck _launch = new() { Text = "安装完成后启动 MangaShelf", Checked = true };
    private readonly FlatButton _go = new() { Primary = true, Text = "开始安装" };
    private readonly FlatButton _cancel = new() { Primary = false, Text = "取消" };
    private readonly FlatCloseButton _close = new();
    private readonly FlatProgress _bar = new();
    private readonly Label _status = new();
    private readonly Label _space = new();

    private Step _step = Step.Ready;
    private string _error = "";
    private string _note = "";
    private string _installedDir = "";
    private CancellationTokenSource? _cts;
    private bool _busy;

    public SetupForm(string? presetDir = null)
    {
        Text = Payload.AppName + " 安装程序";
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.White;
        ClientSize = new Size(W, H);
        AutoScaleMode = AutoScaleMode.Dpi;
        AutoScaleDimensions = new SizeF(96f, 96f);
        DoubleBuffered = true;
        KeyPreview = true;

        var icon = new PictureBox
        {
            Bounds = new Rectangle(28, 24, 40, 40),
            SizeMode = PictureBoxSizeMode.Zoom,
            BackColor = Color.Transparent,
        };
        try { icon.Image = Icon.ExtractAssociatedIcon(Application.ExecutablePath)?.ToBitmap(); }
        catch { /* 没有图标也能装 */ }

        var title = MakeLabel(Payload.AppName, Theme.H1, Theme.Ink, ContentAlignment.MiddleLeft);
        title.Bounds = new Rectangle(82, 20, 300, 32);

        var sub = MakeLabel("本地漫画书架 · 安装程序", Theme.Body, Theme.Ink2, ContentAlignment.MiddleLeft);
        sub.Bounds = new Rectangle(82, 52, 340, 22);

        var whereLabel = MakeLabel("安装位置", Theme.BodyStrong, Theme.Ink, ContentAlignment.MiddleLeft);
        whereLabel.Bounds = new Rectangle(28, 104, 200, 22);

        _path.Bounds = new Rectangle(28, 130, 470, 40);
        _path.Text = ResolveDir(presetDir);

        _browse.Bounds = new Rectangle(508, 130, 84, 40);
        _browse.Click += (_, _) => BrowseFolder();

        _space.Bounds = new Rectangle(28, 176, 564, 20);
        _space.Font = Theme.Small;
        _space.ForeColor = Theme.Muted;
        _space.BackColor = Color.White;
        _space.Text = $"需要约 {Payload.FormatSize(Payload.TotalBytes)} 磁盘空间";

        _desktop.Bounds = new Rectangle(28, 208, 420, 24);
        _desktop.BackColor = Color.White;
        _launch.Bounds = new Rectangle(28, 236, 420, 24);
        _launch.BackColor = Color.White;

        _bar.Bounds = new Rectangle(28, 288, 564, 8);
        _bar.Visible = false;

        _status.Bounds = new Rectangle(28, 302, 564, 62);
        _status.Font = Theme.Small;
        _status.ForeColor = Theme.Ink2;
        _status.BackColor = Color.White;
        _status.Text = "";

        _cancel.Bounds = new Rectangle(392, 372, 96, 36);
        _go.Bounds = new Rectangle(496, 372, 96, 36);
        _close.Bounds = new Rectangle(W - 46, 16, 28, 28);

        _cancel.Click += (_, _) => OnCancelButton();
        _close.Click += (_, _) => OnCancelButton();
        _go.Click += (_, _) => OnPrimaryButton();

        Controls.AddRange(new Control[]
        {
            icon, title, sub, whereLabel, _path, _browse, _space,
            _desktop, _launch, _bar, _status, _cancel, _go, _close,
        });

        KeyDown += (_, e) =>
        {
            if (e.KeyCode == Keys.Escape && !_busy) Close();
        };

        ApplyStep();
    }

    // ---------------------------------------------------------------- 目录

    private static string ResolveDir(string? preset)
    {
        if (!string.IsNullOrWhiteSpace(preset)) return preset!;

        var existing = Payload.ExistingDir();
        if (existing is not null) return existing;

        return Payload.DefaultDir();
    }

    private static string WithAppFolder(string picked)
    {
        var full = picked.Trim().TrimEnd('\\', '/');
        if (full.Length == 0) return Payload.DefaultDir();

        // 用户选的是盘符根目录或某一层现成目录时，自动补一级 MangaShelf，别把文件倒进去
        if (Path.GetFileName(full).Equals(Payload.AppName, StringComparison.OrdinalIgnoreCase)) return full;

        try
        {
            var isEmpty = !Directory.Exists(full) || Directory.GetFileSystemEntries(full).Length == 0;
            if (isEmpty) return Path.Combine(full, Payload.AppName);
            if (File.Exists(Path.Combine(full, Payload.ExeName))) return full;
            return Path.Combine(full, Payload.AppName);
        }
        catch
        {
            return Path.Combine(full, Payload.AppName);
        }
    }

    private void BrowseFolder()
    {
        using var dialog = new FolderBrowserDialog
        {
            Description = "选择 MangaShelf 的安装位置",
            ShowNewFolderButton = true,
            SelectedPath = Directory.Exists(_path.Text) ? _path.Text : Payload.DefaultDir(),
        };
        if (dialog.ShowDialog(this) == DialogResult.OK)
        {
            _path.Text = WithAppFolder(dialog.SelectedPath);
        }
    }

    // ---------------------------------------------------------------- 状态

    private void SetStep(Step step)
    {
        _step = step;
        ApplyStep();
    }

    private void ApplyStep()
    {
        var editable = _step == Step.Ready;

        _path.Enabled = editable;
        _browse.Enabled = editable;
        _desktop.Enabled = editable;
        _launch.Enabled = editable;
        _bar.Visible = _step != Step.Ready;

        switch (_step)
        {
            case Step.Ready:
                _go.Text = "开始安装";
                _go.Enabled = true;
                _cancel.Text = "取消";
                _cancel.Visible = true;
                _status.ForeColor = Theme.Ink2;
                _status.Text = "";
                _bar.Percent = 0;
                break;

            case Step.Installing:
                _go.Text = "安装中…";
                _go.Enabled = false;
                _cancel.Text = "取消";
                _cancel.Visible = true;
                _status.ForeColor = Theme.Ink2;
                break;

            case Step.Done:
                _go.Text = "完成";
                _go.Enabled = true;
                _cancel.Text = "打开文件夹";
                _cancel.Visible = true;
                _bar.Percent = 100;
                _status.ForeColor = Theme.Ok;
                _status.Text = string.IsNullOrEmpty(_note)
                    ? "安装完成，可以在开始菜单里找到 MangaShelf。"
                    : "安装完成。" + _note + "。";
                break;

            case Step.Failed:
                _go.Text = "重试";
                _go.Enabled = true;
                _cancel.Text = "关闭";
                _cancel.Visible = true;
                _status.ForeColor = Theme.AccentDown;
                _status.Text = _error;
                break;
        }

        Invalidate();
    }

    private void OnPrimaryButton()
    {
        switch (_step)
        {
            case Step.Ready:
            case Step.Failed:
                _ = StartInstallAsync();
                break;
            case Step.Done:
                if (_launch.Checked) Payload.Launch(_installedDir);
                Close();
                break;
        }
    }

    private void OnCancelButton()
    {
        if (_step == Step.Installing)
        {
            _cts?.Cancel();
            return;
        }

        // 装完之后取消按钮变成「打开文件夹」：快捷方式万一没建成，这里能直接找到 exe
        if (_step == Step.Done && _installedDir.Length > 0)
        {
            OpenFolder(_installedDir);
            return;
        }

        Close();
    }

    private static void OpenFolder(string dir)
    {
        try
        {
            Process.Start(new ProcessStartInfo("explorer.exe")
            {
                Arguments = $"\"{dir}\"",
                UseShellExecute = true,
            });
        }
        catch
        {
            /* 打不开就算了 */
        }
    }

    // ---------------------------------------------------------------- 安装

    private async Task StartInstallAsync()
    {
        if (_busy) return;

        var dir = _path.Text.Trim().Trim('"');
        if (dir.Length == 0)
        {
            _error = "请先选择安装位置。";
            SetStep(Step.Failed);
            return;
        }

        try
        {
            dir = Path.GetFullPath(dir);
        }
        catch
        {
            _error = "安装位置不是有效的路径，请重新选择。";
            SetStep(Step.Failed);
            return;
        }

        if (!Payload.HasEnoughSpace(dir))
        {
            _error = "磁盘空间不足，需要约 " + Payload.FormatSize(Payload.TotalBytes) + "。";
            SetStep(Step.Failed);
            return;
        }

        // 探测一下位置可不可写，但结果只用来留痕，不用来拦人。
        // 探测本身可能被安全软件、索引服务、云同步干扰而误报，
        // 真写不进去会在解压第一个文件时抛权限异常 —— 那时再要管理员权限，
        // 结论比提前猜准得多（之前就是提前猜，装到自己目录也被弹了 UAC）。
        Payload.CanInstallTo(dir, out var probeReason, out var permissionDenied);
        Program.Trace($"preflight permissionDenied={permissionDenied} dir={dir} reason={probeReason}");

        _busy = true;
        _cts = new CancellationTokenSource();
        SetStep(Step.Installing);
        _status.Text = "正在准备…";
        Cursor = Cursors.WaitCursor;

        var progress = new Progress<(int Pct, string Name)>(t =>
        {
            _bar.Percent = t.Pct;
            _status.Text = $"{t.Name}  {t.Pct}%";
        });

        try
        {
            await Payload.InstallAsync(
                dir,
                (pct, name) => ((IProgress<(int, string)>)progress).Report((pct, name)),
                _cts.Token);

            _status.Text = "正在创建快捷方式…";
            Payload.CreateUninstaller(dir);
            var shortcutOk = Payload.CreateShortcuts(dir, _desktop.Checked);
            if (!_desktop.Checked) Payload.RemoveDesktopShortcut();

            _status.Text = "正在登记卸载信息…";
            var registered = Payload.Register(dir);

            _installedDir = dir;
            _path.Text = dir;

            var notes = new List<string>();
            if (!shortcutOk) notes.Add("快捷方式没能创建（系统策略限制），可直接从安装目录启动");
            if (!registered) notes.Add("未登记到「应用和功能」，可用开始菜单或安装目录里的 Uninstall.exe 卸载");
            _note = notes.Count == 0 ? "" : string.Join("；", notes);

            SetStep(Step.Done);
        }
        catch (OperationCanceledException)
        {
            _error = "安装已取消。";
            SetStep(Step.Failed);
        }
        catch (UnauthorizedAccessException)
        {
            // 真的写不进去（Program Files、盘根、权限特殊的旧目录）：这一步才请求管理员权限
            if (!Program.IsElevated && Program.TryElevate(dir, false, _desktop.Checked))
            {
                Close();   // 安装已交给提权后的自己
                return;
            }

            _error = $"没权限往 {dir} 写文件。可以点「浏览…」换个位置（例如 D:\\MangaShelf），或右键安装程序用管理员身份运行。";
            SetStep(Step.Failed);
        }
        catch (Exception ex)
        {
            _error = Payload.Explain(ex);
            SetStep(Step.Failed);
        }
        finally
        {
            Cursor = Cursors.Default;
            _busy = false;
            _cts?.Dispose();
            _cts = null;
        }
    }

    // ---------------------------------------------------------------- 绘制

    private static Label MakeLabel(string text, Font font, Color color, ContentAlignment align) => new()
    {
        Text = text,
        Font = font,
        ForeColor = color,
        BackColor = Color.White,
        AutoSize = false,
        TextAlign = align,
        UseCompatibleTextRendering = false,
    };

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        var g = e.Graphics;
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        using var pen = new Pen(Theme.Line, 1f);
        g.DrawLine(pen, 0, 88, ClientSize.Width, 88);
        g.DrawRectangle(pen, 0, 0, ClientSize.Width - 1, ClientSize.Height - 1);
    }

    // 无边框窗口：空白处可拖动
    private const int WM_NCLBUTTONDOWN = 0xA1;
    private const int HTCAPTION = 0x2;

    [DllImport("user32.dll")]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        if (e.Button != MouseButtons.Left) return;
        if (_busy) return;
        ReleaseCapture();
        SendMessage(Handle, WM_NCLBUTTONDOWN, (IntPtr)HTCAPTION, IntPtr.Zero);
    }

    // Windows 11 圆角
    private const int DWMWA_WINDOW_CORNER_PREFERENCE = 33;
    private const int DWMWCP_ROUND = 2;

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        try
        {
            var pref = DWMWCP_ROUND;
            DwmSetWindowAttribute(Handle, DWMWA_WINDOW_CORNER_PREFERENCE, ref pref, sizeof(int));
        }
        catch
        {
            /* 老系统没有这个属性，忽略 */
        }
    }
}
