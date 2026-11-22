using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;
using MangaShelf.UI;

namespace MangaShelf;

/// <summary>卸载确认窗口：说明会删什么、可选是否连数据一起删，然后执行</summary>
public sealed class UninstallForm : Form
{
    private const int W = 440;
    private const int H = 276;

    private readonly string _dir;
    private readonly FlatCheck _purge = new() { Text = "同时删除配置文件与阅读进度" };
    private readonly FlatButton _go = new() { Text = "卸载", Primary = true };
    private readonly FlatButton _cancel = new() { Text = "取消", Primary = false };
    private readonly FlatCloseButton _close = new();
    private readonly FlatProgress _bar = new();
    private readonly Label _status = new();
    private bool _busy;

    public UninstallForm(string dir)
    {
        _dir = dir;

        Text = "卸载 " + Uninstaller.AppName;
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
            Bounds = new Rectangle(24, 22, 32, 32),
            SizeMode = PictureBoxSizeMode.Zoom,
            BackColor = Color.Transparent,
        };
        try { icon.Image = Icon.ExtractAssociatedIcon(Application.ExecutablePath)?.ToBitmap(); }
        catch { /* 取不到图标就不显示 */ }

        var title = MakeLabel("卸载 " + Uninstaller.AppName, Theme.H2, Theme.Ink);
        title.Bounds = new Rectangle(68, 20, 300, 30);

        var desc = MakeLabel(
            "将删除程序文件、开始菜单与桌面快捷方式，并从「应用和功能」中移除本程序。",
            Theme.Body, Theme.Ink2);
        desc.Bounds = new Rectangle(24, 84, 392, 44);

        _purge.Bounds = new Rectangle(24, 132, 392, 24);
        _purge.BackColor = Color.White;

        var hint = MakeLabel(
            "不勾选时，漫画库设置和阅读进度会保留，重装后可直接接着用。",
            Theme.Small, Theme.Muted);
        hint.Bounds = new Rectangle(24, 160, 392, 20);

        _bar.Bounds = new Rectangle(24, 196, 392, 8);
        _bar.Visible = false;

        _status.Bounds = new Rectangle(24, 204, 392, 18);
        _status.Font = Theme.Small;
        _status.ForeColor = Theme.Ink2;
        _status.BackColor = Color.White;
        _status.Text = "";

        _cancel.Bounds = new Rectangle(216, 226, 96, 34);
        _go.Bounds = new Rectangle(320, 226, 96, 34);
        _close.Bounds = new Rectangle(W - 48, 16, 28, 28);

        _cancel.Click += (_, _) => { if (!_busy) Close(); };
        _close.Click += (_, _) => { if (!_busy) Close(); };
        _go.Click += OnUninstall;

        Controls.AddRange(new Control[]
        {
            icon, title, desc, _purge, hint, _bar, _status, _cancel, _go, _close,
        });

        KeyDown += (_, e) =>
        {
            if (e.KeyCode == Keys.Escape && !_busy) Close();
        };
    }

    private static Label MakeLabel(string text, Font font, Color color) => new()
    {
        Text = text,
        Font = font,
        ForeColor = color,
        BackColor = Color.White,
        AutoSize = false,
        TextAlign = ContentAlignment.MiddleLeft,
        UseCompatibleTextRendering = false,
    };

    private async void OnUninstall(object? sender, EventArgs e)
    {
        if (_busy) return;
        _busy = true;

        _go.Enabled = false;
        _cancel.Enabled = false;
        _purge.Enabled = false;
        _close.Visible = false;
        _bar.Visible = true;
        Cursor = Cursors.WaitCursor;

        var keepData = !_purge.Checked;
        var progress = new Progress<string>(text =>
        {
            _status.Text = text;
            _bar.Percent = Math.Min(96, _bar.Percent + 18);
        });

        try
        {
            await Task.Run(() => Job.Run(_dir, keepData, text => ((IProgress<string>)progress).Report(text)));
            _bar.Percent = 100;
            _status.Text = "卸载完成";
            Cursor = Cursors.Default;

            var keepHint = keepData
                ? "\n\n配置与阅读进度已保留，重装后可以直接接着用。"
                : "";
            MessageBox.Show(this,
                Uninstaller.AppName + " 已卸载。" + keepHint,
                "卸载完成", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        catch (Exception ex)
        {
            Cursor = Cursors.Default;
            MessageBox.Show(this,
                "卸载过程中出现问题：" + ex.Message +
                "\n\n可以关闭程序后手动删除安装目录：\n" + _dir,
                "卸载", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }

        Close();
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        var g = e.Graphics;
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;

        using (var pen = new Pen(Theme.Line, 1f))
        {
            g.DrawLine(pen, 0, 68, ClientSize.Width, 68);
            g.DrawRectangle(pen, 0, 0, ClientSize.Width - 1, ClientSize.Height - 1);
        }
    }

    // 无边框窗口：按住任意空白处可以拖动
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
        ReleaseCapture();
        SendMessage(Handle, WM_NCLBUTTONDOWN, HTCAPTION, IntPtr.Zero);
    }
}
