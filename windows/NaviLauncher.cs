using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Navi")]
[assembly: AssemblyDescription("Navi desktop launcher")]
[assembly: AssemblyProduct("Navi")]

namespace NaviLauncher
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            string script = Path.Combine(root, "windows", "navi-windows.ps1");
            if (!File.Exists(script))
            {
                MessageBox.Show(
                    "找不到 windows\\navi-windows.ps1，请重新获取完整的 Navi 程序目录。",
                    "Navi",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return;
            }

            bool firstRun =
                !File.Exists(Path.Combine(root, ".venv-win", "Scripts", "pythonw.exe")) ||
                !File.Exists(Path.Combine(root, "frontend", "dist", "index.html"));
            Application.Run(new BootstrapForm(root, script, firstRun));
        }
    }

    internal sealed class BootstrapForm : Form
    {
        private readonly string _root;
        private readonly Process _setupProcess;
        private readonly Timer _timer;
        private readonly StringBuilder _output = new StringBuilder();
        private readonly object _outputLock = new object();
        private Process _desktopProcess;
        private int _stage;
        private int _windowWaitTicks;

        public BootstrapForm(string root, string script, bool firstRun)
        {
            _root = root;
            Text = "Navi 启动器";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ShowInTaskbar = true;
            ClientSize = new Size(430, 132);
            BackColor = Color.White;

            Label title = new Label();
            title.Text = firstRun ? "首次启动正在准备 Navi" : "正在打开 Navi";
            title.Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Regular);
            title.AutoSize = false;
            title.SetBounds(24, 20, 382, 32);
            Controls.Add(title);

            Label detail = new Label();
            detail.Text = firstRun
                ? "首次使用需要准备运行环境，可能需要几分钟。"
                : "正在启动本机服务和桌面窗口。";
            detail.ForeColor = Color.FromArgb(91, 101, 112);
            detail.AutoSize = false;
            detail.SetBounds(24, 54, 382, 24);
            Controls.Add(detail);

            ProgressBar progress = new ProgressBar();
            progress.Style = ProgressBarStyle.Marquee;
            progress.MarqueeAnimationSpeed = 28;
            progress.SetBounds(24, 88, 382, 16);
            Controls.Add(progress);

            _setupProcess = LaunchPowerShell(root, script, "setup", _output, _outputLock);
            _timer = new Timer();
            _timer.Interval = 250;
            _timer.Tick += OnTimerTick;
            _timer.Start();
        }

        private static Process LaunchPowerShell(
            string root,
            string script,
            string operation,
            StringBuilder output,
            object outputLock)
        {
            string powershell = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe");
            if (!File.Exists(powershell))
            {
                powershell = "powershell.exe";
            }

            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = powershell;
            info.Arguments = string.Format(
                "-NoProfile -ExecutionPolicy Bypass -File \"{0}\" {1}",
                script,
                operation);
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;

            Process process = new Process();
            process.StartInfo = info;
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs args)
            {
                Append(output, outputLock, args.Data);
            };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs args)
            {
                Append(output, outputLock, args.Data);
            };
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            return process;
        }

        private static Process LaunchDesktop(string root)
        {
            string pythonw = Path.Combine(root, ".venv-win", "Scripts", "pythonw.exe");
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = pythonw;
            info.Arguments = "-m navi.desktop";
            info.WorkingDirectory = Path.Combine(root, "backend");
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Normal;
            info.EnvironmentVariables["PYTHONPATH"] = Path.Combine(root, "backend");
            info.EnvironmentVariables["PYTHONUTF8"] = "1";

            Process process = new Process();
            process.StartInfo = info;
            process.Start();
            return process;
        }

        private static void Append(StringBuilder output, object outputLock, string line)
        {
            if (String.IsNullOrWhiteSpace(line))
            {
                return;
            }
            lock (outputLock)
            {
                output.AppendLine(line);
                if (output.Length > 12000)
                {
                    output.Remove(0, output.Length - 12000);
                }
            }
        }

        private void OnTimerTick(object sender, EventArgs args)
        {
            if (_stage == 0)
            {
                if (!_setupProcess.HasExited)
                {
                    return;
                }
                _setupProcess.WaitForExit();
                WriteLauncherLog();
                if (_setupProcess.ExitCode != 0)
                {
                    _timer.Stop();
                    ShowFailure();
                    Close();
                    return;
                }
                try
                {
                    _desktopProcess = LaunchDesktop(_root);
                }
                catch (Exception error)
                {
                    _timer.Stop();
                    ShowFailure(error.Message);
                    Close();
                    return;
                }
                _stage = 1;
            }

            _windowWaitTicks++;
            if (NaviWindowIsOpen() || _windowWaitTicks > 80)
            {
                _timer.Stop();
                Close();
            }
        }

        private void WriteLauncherLog()
        {
            try
            {
                string data = Path.Combine(_root, "data");
                Directory.CreateDirectory(data);
                string details;
                lock (_outputLock)
                {
                    details = _output.ToString();
                }
                string entry = string.Format(
                    "[{0:yyyy-MM-dd HH:mm:ss}] setup exit={1}{2}{3}{2}",
                    DateTime.Now,
                    _setupProcess.ExitCode,
                    details,
                    Environment.NewLine);
                File.AppendAllText(Path.Combine(data, "launcher.log"), entry, Encoding.UTF8);
            }
            catch
            {
            }
        }

        private static bool NaviWindowIsOpen()
        {
            Process[] processes = Process.GetProcessesByName("pythonw");
            try
            {
                foreach (Process process in processes)
                {
                    if (process.MainWindowTitle == "Navi" && process.MainWindowHandle != IntPtr.Zero)
                    {
                        return true;
                    }
                }
            }
            finally
            {
                foreach (Process process in processes)
                {
                    process.Dispose();
                }
            }
            return false;
        }

        private void ShowFailure(string extra = "")
        {
            string details;
            lock (_outputLock)
            {
                details = _output.ToString().Trim();
            }
            if (details.Length > 1800)
            {
                details = details.Substring(details.Length - 1800);
            }
            string message = "Navi 启动失败。请查看 data\\gateway.log。";
            if (details.Length > 0)
            {
                message += Environment.NewLine + Environment.NewLine + details;
            }
            if (!String.IsNullOrWhiteSpace(extra))
            {
                message += Environment.NewLine + Environment.NewLine + extra;
            }
            MessageBox.Show(message, "Navi", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                if (_timer != null)
                {
                    _timer.Dispose();
                }
                if (_setupProcess != null)
                {
                    _setupProcess.Dispose();
                }
                if (_desktopProcess != null)
                {
                    _desktopProcess.Dispose();
                }
            }
            base.Dispose(disposing);
        }
    }
}
