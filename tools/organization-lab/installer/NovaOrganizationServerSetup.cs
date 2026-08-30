using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Principal;
using System.Windows.Forms;

internal static class Program
{
    private const string ProductDirectory = "Nova\\OrganizationServer";

    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try
        {
            if (!IsAdministrator())
                throw new InvalidOperationException("Administrator elevation is required.");

            string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), ProductDirectory);
            Directory.CreateDirectory(root);
            string script = Path.Combine(root, "NovaOrganizationServerSetup.ps1");
            string profiles = Path.Combine(root, "profiles.manifest.json");
            Extract("Nova.Setup.ps1", script);
            Extract("Nova.Profiles.json", profiles);

            string installedExe = Path.Combine(root, "NovaOrganizationServerSetup.exe");
            string currentExe = Assembly.GetExecutingAssembly().Location;
            if (!String.Equals(currentExe, installedExe, StringComparison.OrdinalIgnoreCase))
                File.Copy(currentExe, installedExe, true);

            string action = args.Length == 0 ? "install" : args[0];
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                "WindowsPowerShell\\v1.0\\powershell.exe");
            start.Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\" " + action;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            using (Process process = Process.Start(start))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Nova Organization Server Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    private static bool IsAdministrator()
    {
        WindowsIdentity identity = WindowsIdentity.GetCurrent();
        WindowsPrincipal principal = new WindowsPrincipal(identity);
        return principal.IsInRole(WindowsBuiltInRole.Administrator);
    }

    private static void Extract(string resourceName, string destination)
    {
        using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream(resourceName))
        {
            if (source == null) throw new InvalidOperationException("Embedded installer resource missing: " + resourceName);
            using (FileStream target = new FileStream(destination, FileMode.Create, FileAccess.Write, FileShare.None))
                source.CopyTo(target);
        }
    }
}
