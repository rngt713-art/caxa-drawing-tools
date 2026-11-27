using System;
using System.IO;
using System.Reflection;
using CAXA.A5.EAP.Service;

class Smoke
{
    static Smoke()
    {
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            Assembly a = Resolve(e.Name);
            return a;
        };
    }

    static readonly string[] ProbeDirs =
    {
        @"C:\Program Files (x86)\Common Files\CAXA Shared\CAXA Integration\EAPInterface\1.0\EAP 2011r3",
        @"C:\Windows\Microsoft.NET\assembly\GAC_32",
        @"C:\Windows\Microsoft.NET\assembly\GAC_MSIL"
    };

    static Assembly Resolve(string nameText)
    {
        AssemblyName name = new AssemblyName(nameText);
        foreach (string dir in ProbeDirs)
        {
            string path = Path.Combine(dir, name.Name + ".dll");
            if (File.Exists(path)) return Assembly.LoadFrom(path);
            if (dir.EndsWith("GAC_32") || dir.EndsWith("GAC_MSIL"))
            {
                string gacDir = Path.Combine(dir, name.Name);
                if (Directory.Exists(gacDir))
                {
                    foreach (string dll in Directory.GetFiles(gacDir, "*.dll", SearchOption.AllDirectories))
                    {
                        try
                        {
                            Assembly a = Assembly.LoadFrom(dll);
                            if (a.GetName().Name == name.Name) return a;
                        }
                        catch { }
                    }
                }
            }
        }
        return null;
    }

    static readonly string LogPath = Path.Combine(Path.GetTempPath(), "caxa_smoke.log");

    static void Log(string s)
    {
        try
        {
            File.AppendAllText(LogPath, s + Environment.NewLine);
        }
        catch { }
        Console.WriteLine(s);
        Console.Out.Flush();
    }

    [STAThread]
    static void Main(string[] args)
    {
        try { File.Delete(LogPath); } catch { }
        Log("step1: start");
        try
        {
            Log("step2: new EAPService...");
            EAPService svc = new EAPService();
            Log("step3: created: " + svc.GetType().FullName);
            Log("step4: Version=" + svc.Version + " HasLogin=" + svc.HasLogin + " App=" + svc.ApplicationName);
            svc.ApplicationName = "CAXAVAULT";
            Log("step5: appname set, trying dummy login...");
            string u = "__caxa_probe__", p = "__nope__";
            string serverIp = args.Length > 0
                ? args[0]
                : (Environment.GetEnvironmentVariable("CAXA_SERVER_IP") ?? "127.0.0.1");
            bool ok = svc.Login(ref u, ref p, serverIp, 8900);
            Log("step6: login=" + ok + " user=" + u + " HasLogin=" + svc.HasLogin + " LastLoginUser=" + svc.LastLoginUser);
        }
        catch (Exception ex)
        {
            for (Exception cur = ex; cur != null; cur = cur.InnerException)
            {
                Log("EX: " + cur.GetType().FullName + " | " + cur.Message);
                Log("STACK: " + cur.StackTrace);
            }
        }
        Log("done");
    }
}
