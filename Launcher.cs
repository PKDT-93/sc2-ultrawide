using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class Launcher
{
    internal static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
    internal static readonly string Logs = Path.Combine(Root, "logs");

    internal static async Task<Tuple<int, string>> Run(string command)
    {
        string script = Path.Combine(Root, "sc2_ultrawide.py");
        if (!File.Exists(script))
            throw new FileNotFoundException("请将 EXE 与 sc2_ultrawide.py、ultrawide 文件夹放在一起。", script);
        Directory.CreateDirectory(Logs);
        var info = new ProcessStartInfo("py.exe", "-3-64 -B \"" + script + "\" " + command);
        info.WorkingDirectory = Root;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        info.StandardOutputEncoding = Encoding.UTF8;
        info.StandardErrorEncoding = Encoding.UTF8;
        info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        info.EnvironmentVariables["SC2_ULTRAWIDE_LOG_DIR"] = Logs;
        using (var process = Process.Start(info))
        {
            Task<string> output = process.StandardOutput.ReadToEndAsync();
            Task<string> errors = process.StandardError.ReadToEndAsync();
            await Task.Run(() => process.WaitForExit());
            string text = await output + await errors;
            File.WriteAllText(Path.Combine(Logs, "last-command.txt"),
                DateTimeOffset.Now.ToString("o") + " " + command + "\r\n" + text, Encoding.UTF8);
            return Tuple.Create(process.ExitCode, text);
        }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        // CLI smoke-test mode: status is read-only; all other launches open the GUI.
        if (args.Length == 1 && args[0] == "--status")
        {
            try { return Run("status").GetAwaiter().GetResult().Item1; }
            catch (Exception error)
            {
                Directory.CreateDirectory(Logs);
                File.WriteAllText(Path.Combine(Logs, "launcher-error.txt"), error.ToString());
                return 1;
            }
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new LauncherForm());
        return 0;
    }
}

internal sealed class LauncherForm : Form
{
    readonly TextBox output = new TextBox();
    readonly FlowLayoutPanel actions = new FlowLayoutPanel();
    readonly Label state = new Label();
    bool busy;

    internal LauncherForm()
    {
        Text = "星际 II 超宽工具 · 国服 98370 预览版";
        ClientSize = new Size(780, 510);
        MinimumSize = new Size(740, 470);
        StartPosition = FormStartPosition.CenterScreen;
        Font = new Font("Microsoft YaHei UI", 10F);

        var heading = new Label { Text = "先启动游戏，再启用超宽", Dock = DockStyle.Top,
            Height = 48, Padding = new Padding(16, 12, 0, 0), Font = new Font(Font, FontStyle.Bold) };
        var description = new Label { Dock = DockStyle.Top, Height = 70, Padding = new Padding(16, 4, 16, 0),
            Text = "游戏需设为窗口化全屏。启用后助手自动修复每关的底部 HUD。\r\n关闭本窗口不会停止助手；退出游戏后助手自动结束。" };
        var risk = new Label { Dock = DockStyle.Bottom, Height = 46, Padding = new Padding(16, 6, 16, 4),
            ForeColor = Color.DarkRed, Text = "仅限单人测试。工具会修改游戏内存，不能保证账号安全。\r\n不修改游戏文件，不设置开机启动，不自动提权。" };
        actions.Dock = DockStyle.Top;
        actions.Height = 52;
        actions.Padding = new Padding(12, 3, 0, 0);
        AddButton("启用超宽 + 自动修复 HUD", "apply", 235);
        AddButton("只读查看状态", "status", 140);
        AddButton("恢复 16:9", "revert", 120);
        state.Dock = DockStyle.Top;
        state.Height = 32;
        state.Padding = new Padding(16, 4, 0, 0);
        state.Text = "未执行操作";
        output.Multiline = true;
        output.ReadOnly = true;
        output.ScrollBars = ScrollBars.Vertical;
        output.Dock = DockStyle.Fill;
        output.Font = new Font("Consolas", 10F);
        output.BackColor = Color.White;
        output.Text = "先点击“只读查看状态”可检查游戏与助手。\r\n日志保存在同目录 logs 文件夹。";
        Controls.Add(output);
        Controls.Add(state);
        Controls.Add(actions);
        Controls.Add(description);
        Controls.Add(heading);
        Controls.Add(risk);
        FormClosing += (sender, e) => { if (busy) e.Cancel = true; };
    }

    void AddButton(string label, string command, int width)
    {
        var button = new Button { Text = label, Width = width, Height = 36, Margin = new Padding(4) };
        button.Click += async (sender, e) => await Execute(command);
        actions.Controls.Add(button);
    }

    async Task Execute(string command)
    {
        if (busy) return;
        busy = true;
        actions.Enabled = false;
        state.Text = "正在执行，请稍候…";
        try
        {
            var result = await Launcher.Run(command);
            output.Text = result.Item2.Replace("\r\n", "\n").Replace("\n", "\r\n");
            state.Text = result.Item1 == 0 ? "操作完成" : "未成功，请查看下方原因；不会自动提权";
        }
        catch (Exception error)
        {
            state.Text = "启动失败";
            output.Text = error.Message + "\r\n需要已安装的 64 位 Python 3 和 Python Launcher (py.exe)。";
        }
        finally { busy = false; actions.Enabled = true; }
    }
}
