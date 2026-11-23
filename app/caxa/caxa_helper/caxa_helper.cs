using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Automation;
using CAXA.A5.EAP.Common;
using CAXA.A5.EAP.Service;
using Microsoft.Win32;

class CaxaHelper
{
    static readonly string[] ProbeDirs =
    {
        @"C:\Program Files (x86)\Common Files\CAXA Shared\CAXA Integration\EAPInterface\1.0\EAP 2011r3",
        @"D:\Program Files (x86)\Common Files\CAXA Shared\CAXA Integration\EAPInterface\1.0\EAP 2011r3",
        @"E:\Program Files (x86)\Common Files\CAXA Shared\CAXA Integration\EAPInterface\1.0\EAP 2011r3",
        @"C:\Program Files\Common Files\CAXA Shared\CAXA Integration\EAPInterface\1.0\EAP 2011r3",
        @"D:\Program Files\Common Files\CAXA Shared\CAXA Integration\EAPInterface\1.0\EAP 2011r3",
        @"C:\Windows\Microsoft.NET\assembly\GAC_32",
        @"C:\Windows\Microsoft.NET\assembly\GAC_MSIL"
    };

    static CaxaHelper()
    {
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) => Resolve(e.Name);
    }

    static Assembly Resolve(string nameText)
    {
        try
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
        }
        catch { }
        return null;
    }

    static JavaScriptSerializer _json = new JavaScriptSerializer();
    static EAPService _svc;

    static Dictionary<string, object> Ok(object extra = null)
    {
        var d = new Dictionary<string, object> { { "ok", true } };
        if (extra != null)
        {
            foreach (var kv in (Dictionary<string, object>)extra) d[kv.Key] = kv.Value;
        }
        return d;
    }

    static Dictionary<string, object> Err(string msg)
    {
        return new Dictionary<string, object> { { "ok", false }, { "error", msg } };
    }

    static Dictionary<string, object> WriteError(
        string message,
        string stage,
        Dictionary<string, object> createdIds = null,
        int objectId = 0,
        string detail = "")
    {
        var result = Err(message);
        result["stage"] = stage;
        result["partial"] = true;
        result["uncertain"] = true;
        if (objectId > 0) result["object_id"] = objectId;
        if (createdIds != null && createdIds.Count > 0)
            result["created_ids"] = createdIds;
        if (!string.IsNullOrEmpty(detail)) result["detail"] = detail;
        return result;
    }

    static Dictionary<string, object> AttrDict(IEAPEntityBase e)
    {
        var d = new Dictionary<string, object>
        {
            { "class", e.GetType().Name },
            { "class_id", e.ClassID },
            { "object_id", e.ObjectID }
        };
        try { d["primary"] = e.GetPrimaryAttributesAsString("|"); } catch { }
        foreach (string attr in new[] { "图号", "名称", "成品代码", "整机代码", "上级图号", "物料代码", "规格型号" })
        {
            try
            {
                string v = e.GetValueAsString(attr);
                if (!string.IsNullOrEmpty(v)) d[attr] = v;
            }
            catch { }
        }
        IEAPProductEntity p = e as IEAPProductEntity;
        if (p != null)
        {
            d["identity"] = p.IdentityNo;
            d["name"] = p.Name;
        }
        else
        {
            IEAPPartEntity pt = e as IEAPPartEntity;
            if (pt != null)
            {
                d["identity"] = pt.IdentityNo;
                d["name"] = pt.Name;
                d["is_borrowed"] = pt.IsBorrowed;
            }
            else
            {
                IEAPDrawingEntity dr = e as IEAPDrawingEntity;
                if (dr != null)
                {
                    d["identity"] = dr.DrawNo;
                    d["name"] = dr.Name;
                    d["file_name"] = dr.FileName;
                    d["file_type"] = dr.FileType;
                }
                else
                {
                    IEAPFolderEntity f = e as IEAPFolderEntity;
                    if (f != null)
                    {
                        d["name"] = f.Name;
                        d["description"] = f.Description;
                    }
                    else
                    {
                        IEAPProductFolderEntity pf = e as IEAPProductFolderEntity;
                        if (pf != null)
                        {
                            d["identity"] = pf.IdentityNo;
                            d["name"] = pf.Name;
                        }
                    }
                }
            }
        }
        return d;
    }

    static Dictionary<string, object> DumpFolder(IEAPFolderEntity f, int depth, int maxDepth, HashSet<string> visited)
    {
        var d = AttrDict(f);
        d["kind"] = "folder";
        if (depth >= maxDepth) return d;
        string key = "F" + f.ClassID + ":" + f.ObjectID;
        if (!visited.Add(key)) { d["loop"] = true; return d; }
        var children = new List<object>();
        try
        {
            foreach (var sub in f.Children ?? new IEAPFolderEntity[0]) children.Add(DumpFolder(sub, depth + 1, maxDepth, visited));
        }
        catch { }
        try
        {
            foreach (var dr in f.GetAllMaterDrawing() ?? new IEAPDrawingEntity[0]) children.Add(DumpDrawing(dr, depth + 1, maxDepth));
        }
        catch { }
        d["children"] = children;
        return d;
    }

    static Dictionary<string, object> DumpDrawing(IEAPDrawingEntity dr, int depth, int maxDepth)
    {
        var d = AttrDict(dr);
        d["kind"] = "drawing";
        return d;
    }

    static Dictionary<string, object> DumpPart(IEAPPartEntity pt, int depth, int maxDepth, HashSet<string> visited)
    {
        var d = AttrDict(pt);
        d["kind"] = "part";
        if (depth >= maxDepth) return d;
        string key = "P" + pt.ClassID + ":" + pt.ObjectID;
        if (!visited.Add(key)) { d["loop"] = true; return d; }
        var children = new List<object>();
        try
        {
            foreach (var sub in pt.Children(null) ?? new IEAPPart2PartEntity[0])
            {
                IEAPPartEntity sp = null;
                try { sp = sub.RefSubPart; } catch { }
                if (sp != null) children.Add(DumpPart(sp, depth + 1, maxDepth, visited));
            }
        }
        catch { }
        try
        {
            foreach (var comp in pt.GetAllMaterDrawing() ?? new CompositeEntity[0])
            {
                IEAPDrawingEntity de = comp.Entity as IEAPDrawingEntity;
                if (de != null) children.Add(DumpDrawing(de, depth + 1, maxDepth));
            }
        }
        catch { }
        d["children"] = children;
        return d;
    }

    static Dictionary<string, object> DumpProduct(IEAPProductEntity p, int depth, int maxDepth, HashSet<string> visited)
    {
        var d = AttrDict(p);
        d["kind"] = "product";
        if (depth >= maxDepth) return d;
        string key = "PR" + p.ClassID + ":" + p.ObjectID;
        if (!visited.Add(key)) { d["loop"] = true; return d; }
        var children = new List<object>();
        try
        {
            foreach (var f in p.RelatedFolders() ?? new IEAPFolderEntity[0]) children.Add(DumpFolder(f, depth + 1, maxDepth, visited));
        }
        catch { }
        try
        {
            foreach (var pf in p.RelatedProductFolders() ?? new IEAPProductFolderEntity[0])
            {
                var pd = AttrDict(pf);
                pd["kind"] = "product_folder";
                children.Add(pd);
            }
        }
        catch { }
        try
        {
            foreach (var dr in p.RelatedDrawings() ?? new IEAPDrawingEntity[0]) children.Add(DumpDrawing(dr, depth + 1, maxDepth));
        }
        catch { }
        try
        {
            foreach (var link in p.RelatedRootParts((IEAPPrd2PartEntity)null) ?? new IEAPPrd2PartEntity[0])
            {
                IEAPPartEntity pt = null;
                try { pt = link.RefPart; } catch { }
                if (pt != null)
                {
                    var pd = DumpPart(pt, depth + 1, maxDepth, visited);
                    try { pd["link_reference"] = link.Reference; } catch { }
                    children.Add(pd);
                }
            }
        }
        catch { }
        d["children"] = children;
        return d;
    }

    static IEAPFolderEntity FindFolderById(IEAPFolderEntity root, int id, HashSet<string> visited)
    {
        if (root == null) return null;
        if (root.ObjectID == id) return root;
        string key = "F" + root.ClassID + ":" + root.ObjectID;
        if (!visited.Add(key)) return null;
        try
        {
            foreach (var sub in root.Children ?? new IEAPFolderEntity[0])
            {
                IEAPFolderEntity f = FindFolderById(sub, id, visited);
                if (f != null) return f;
            }
        }
        catch { }
        return null;
    }

    static IEAPFolderEntity FindFolderById(int id)
    {
        foreach (var root in _svc.GetAllRootFolders() ?? new IEAPFolderEntity[0])
        {
            IEAPFolderEntity f = FindFolderById(root, id, new HashSet<string>());
            if (f != null) return f;
        }
        return null;
    }

    static IEAPDrawingEntity FindDrawingInFolder(IEAPFolderEntity f, int id, HashSet<string> visited)
    {
        if (f == null) return null;
        string key = "F" + f.ClassID + ":" + f.ObjectID;
        if (!visited.Add(key)) return null;
        try
        {
            foreach (var de in f.GetAllMaterDrawing() ?? new IEAPDrawingEntity[0])
                if (de != null && de.ObjectID == id) return de;
            foreach (var sub in f.Children ?? new IEAPFolderEntity[0])
            {
                IEAPDrawingEntity de = FindDrawingInFolder(sub, id, visited);
                if (de != null) return de;
            }
        }
        catch { }
        return null;
    }

    static IEAPDrawingEntity FindDrawingById(int id)
    {
        foreach (var prod in _svc.GetAllRootProducts() ?? new IEAPProductEntity[0])
        {
            try
            {
                foreach (var dr in prod.RelatedDrawings() ?? new IEAPDrawingEntity[0])
                    if (dr.ObjectID == id) return dr;
                foreach (var f in prod.RelatedFolders() ?? new IEAPFolderEntity[0])
                {
                    IEAPDrawingEntity de = FindDrawingInFolder(f, id, new HashSet<string>());
                    if (de != null) return de;
                }
            }
            catch { }
        }
        foreach (var f in _svc.GetAllRootFolders() ?? new IEAPFolderEntity[0])
        {
            IEAPDrawingEntity de = FindDrawingInFolder(f, id, new HashSet<string>());
            if (de != null) return de;
        }
        return null;
    }

    static readonly Guid PlmComProductStructureClsid =
        new Guid("F7950E30-7112-4D20-8610-2196CBDE2511");

    static string FindPlmComApiDll()
    {
        try
        {
            using (var baseKey = RegistryKey.OpenBaseKey(
                RegistryHive.LocalMachine,
                RegistryView.Registry32))
            using (var key = baseKey.OpenSubKey(
                @"SOFTWARE\Classes\CLSID\{" +
                PlmComProductStructureClsid.ToString("D") +
                @"}\InprocServer32"))
            {
                string registered = key != null
                    ? Convert.ToString(key.GetValue(null))
                    : "";
                if (!string.IsNullOrWhiteSpace(registered) && File.Exists(registered))
                    return registered;
            }
        }
        catch { }
        var roots = new[]
        {
            Environment.GetEnvironmentVariable("CommonProgramFiles(x86)"),
            Environment.GetEnvironmentVariable("CommonProgramFiles"),
            @"C:\Program Files (x86)\Common Files",
            @"D:\Program Files (x86)\Common Files",
            @"E:\Program Files (x86)\Common Files",
            @"C:\Program Files\Common Files",
            @"D:\Program Files\Common Files"
        };
        foreach (string root in roots)
        {
            if (string.IsNullOrWhiteSpace(root)) continue;
            string path = Path.Combine(
                root,
                "CAXA Shared",
                "CAXA EAP",
                "1.0",
                "template",
                "script",
                "PlmComApi.dll");
            if (File.Exists(path)) return path;
        }
        return "";
    }

    static List<string> GetCaxaDependencyDirectories(string dllPath)
    {
        var directories = new List<string>();
        try
        {
            if (string.IsNullOrWhiteSpace(dllPath) || !File.Exists(dllPath))
                return directories;
            string scriptDir = Path.GetDirectoryName(Path.GetFullPath(dllPath));
            if (!string.IsNullOrWhiteSpace(scriptDir))
            {
                directories.Add(scriptDir);
                var templateDir = Directory.GetParent(scriptDir);
                var eap10Dir = templateDir != null
                    ? Directory.GetParent(templateDir.FullName)
                    : null;
                if (eap10Dir != null)
                    directories.Add(Path.Combine(eap10Dir.FullName, "bin"));
            }

            string programFilesX86 =
                Environment.GetEnvironmentVariable("ProgramFiles(x86)");
            if (!string.IsNullOrWhiteSpace(programFilesX86))
            {
                string caxaPlm = Path.Combine(
                    programFilesX86, "CAXA", "CAXA PLM", "2011");
                directories.Add(Path.Combine(caxaPlm, "CAXABOM", "bin"));
                directories.Add(Path.Combine(caxaPlm, "CAXAEDM", "bin"));
            }
        }
        catch
        {
            // Return the directories collected before the failure.
        }
        return directories;
    }

    static void EnsureCaxaDependencyPath(string dllPath)
    {
        try
        {
            var directories = GetCaxaDependencyDirectories(dllPath);
            string current = Environment.GetEnvironmentVariable("PATH") ?? "";
            var currentKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string item in current.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries))
                currentKeys.Add(item.TrimEnd('\\'));

            var prefix = new List<string>();
            foreach (string directory in directories)
            {
                if (string.IsNullOrWhiteSpace(directory) ||
                    !Directory.Exists(directory))
                    continue;
                string full = Path.GetFullPath(directory).TrimEnd('\\');
                if (currentKeys.Add(full))
                    prefix.Add(full);
            }
            if (prefix.Count == 0)
                return;
            Environment.SetEnvironmentVariable(
                "PATH",
                string.Join(";", prefix.ToArray()) +
                    (string.IsNullOrWhiteSpace(current) ? "" : ";" + current),
                EnvironmentVariableTarget.Process);
        }
        catch
        {
            // A malformed PATH must not prevent the normal login error path.
        }
    }

    static bool TryActivateExistingPlmCom()
    {
        object instance = null;
        try
        {
            Type type = Type.GetTypeFromCLSID(
                PlmComProductStructureClsid,
                true);
            instance = Activator.CreateInstance(type);
            return instance != null;
        }
        catch
        {
            return false;
        }
        finally
        {
            if (instance != null)
            {
                try { Marshal.FinalReleaseComObject(instance); }
                catch { }
            }
        }
    }

    static Dictionary<string, object> RegisterPlmCom()
    {
        string dllPath = FindPlmComApiDll();
        EnsureCaxaDependencyPath(dllPath);
        var status = new Dictionary<string, object>
        {
            { "registered", false },
            { "dll", dllPath ?? "" },
            { "error", "" },
            { "exit_code", null },
            { "elevated", false }
        };
        if (TryActivateExistingPlmCom())
        {
            status["registered"] = true;
            status["registration_attempted"] = false;
            status["existing_registration_verified"] = true;
            return status;
        }
        status["registration_attempted"] = true;
        if (string.IsNullOrWhiteSpace(dllPath) || !File.Exists(dllPath))
        {
            status["error"] = "PlmComApi.dll not found";
            return status;
        }

        string windowsDir = Environment.GetFolderPath(
            Environment.SpecialFolder.Windows);
        string regsvr = Path.Combine(windowsDir, "SysWOW64", "regsvr32.exe");
        string cmdExe = Path.Combine(windowsDir, "System32", "cmd.exe");
        if (!File.Exists(regsvr) || !File.Exists(cmdExe))
        {
            status["error"] = "32-bit regsvr32.exe or cmd.exe not found";
            return status;
        }

        string tempRoot = Path.GetTempPath();
        string token = Guid.NewGuid().ToString("N");
        string commandPath = Path.Combine(tempRoot, "caxa_register_" + token + ".cmd");
        string resultPath = Path.Combine(tempRoot, "caxa_register_" + token + ".txt");
        var dependencyDirs = GetCaxaDependencyDirectories(dllPath);
        string explicitPath = string.Join(";", dependencyDirs.ToArray());
        var lines = new List<string>
        {
            "@echo off",
            "set \"PATH=" + explicitPath + ";%PATH%\"",
            "\"" + regsvr + "\" /s \"" + dllPath + "\"",
            "set \"CAXA_REGISTER_RC=%ERRORLEVEL%\"",
            "> \"" + resultPath + "\" echo %CAXA_REGISTER_RC%",
            "exit /b %CAXA_REGISTER_RC%"
        };
        File.WriteAllLines(commandPath, lines.ToArray(), Encoding.Default);
        try
        {
            var start = new ProcessStartInfo
            {
                FileName = cmdExe,
                Arguments = "/d /c \"\"" + commandPath + "\"\"",
                UseShellExecute = true,
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden
            };
            using (var process = Process.Start(start))
            {
                status["elevated"] = true;
                if (process == null || !process.WaitForExit(60000))
                {
                    status["error"] = "registration process timed out";
                    return status;
                }
            }
            if (!File.Exists(resultPath))
            {
                status["error"] = "elevated registration returned no exit code";
                return status;
            }
            string resultText = File.ReadAllText(resultPath, Encoding.Default).Trim();
            int exitCode;
            if (!Int32.TryParse(resultText, out exitCode))
            {
                status["error"] = "invalid registration exit code: " + resultText;
                return status;
            }
            status["exit_code"] = exitCode;
            status["registration_method"] = "elevated_cmd_explicit_path";
            status["dependency_dirs"] = dependencyDirs.ToArray();
            if (exitCode != 0)
            {
                status["error"] =
                    "regsvr32 returned exit code " +
                    exitCode.ToString();
                return status;
            }
        }
        catch (Exception ex)
        {
            status["error"] = "elevation or registration failed: " + ex.Message;
            return status;
        }
        finally
        {
            try { File.Delete(commandPath); } catch { }
            try { File.Delete(resultPath); } catch { }
        }

        status["registered"] = true;
        return status;
    }

    static Dictionary<string, object> CmdEnsurePlmComRegistered()
    {
        var status = RegisterPlmCom();
        if (!Convert.ToBoolean(status["registered"]))
        {
            var failed = Err(
                "CAXA PlmComApi.dll 注册失败：" +
                Convert.ToString(status["error"]));
            failed["stage"] = "plm_com_registration";
            failed["plm_com"] = status;
            return failed;
        }
        var ok = Ok();
        ok["plm_com"] = status;
        return ok;
    }

    static Dictionary<string, object> CmdLogin(Dictionary<string, object> a)
    {
        string ip = (string)a["server_ip"];
        int port = Convert.ToInt32(a["server_port"]);
        string user = (string)a["user"];
        string pass = (string)a["password"];
        EnsureCaxaDependencyPath(FindPlmComApiDll());
        if (_svc == null) _svc = new EAPService();
        // 新进程/换账号都要重新登录；旧会话先结束，避免残留身份。
        try
        {
            if (_svc.HasLogin) _svc.Logout();
        }
        catch { }
        _svc.ApplicationName = "CAXAVAULT";
        bool ok = false;
        _loginInProgress = true;
        try
        {
            ok = _svc.Login(ref user, ref pass, ip, port);
        }
        catch (Exception ex)
        {
            var failed = Err("login failed: " + ex.Message);
            failed["stage"] = "login";
            failed["detail"] = ex.ToString();
            failed["logged_in"] = false;
            return failed;
        }
        finally
        {
            _loginInProgress = false;
        }
        if (!ok || !_svc.HasLogin)
        {
            var failed = Err("login returned false");
            failed["stage"] = "login";
            failed["logged_in"] = false;
            return failed;
        }
        try
        {
            var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
            var session = engine != null ? engine.CurSession : null;
            if (session != null) session.IsSilentMode = true;
        }
        catch { }
        try { _svc.SetWindowVisible(false); } catch { }
        var loggedIn = Ok(new Dictionary<string, object>
        {
            { "version", _svc.Version },
            { "user", user },
            { "logged_in", true },
            { "server_ip", ip },
            { "server_port", port }
        });
        loggedIn["plm_com"] = new Dictionary<string, object>
        {
            { "managed_by_device_registration", true }
        };
        return loggedIn;
    }

    static Dictionary<string, object> CmdLogout()
    {
        if (_svc != null && _svc.HasLogin) _svc.Logout();
        return Ok();
    }

    static Dictionary<string, object> CmdDumpTree(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int maxDepth = a.ContainsKey("max_depth") ? Convert.ToInt32(a["max_depth"]) : 4;
        var visited = new HashSet<string>();
        var roots = new List<object>();
        foreach (var p in _svc.GetAllRootProducts() ?? new IEAPProductEntity[0]) roots.Add(DumpProduct(p, 0, maxDepth, visited));
        foreach (var pf in _svc.GetAllRootProductFolders() ?? new IEAPProductFolderEntity[0])
        {
            var pd = AttrDict(pf);
            pd["kind"] = "product_folder";
            roots.Add(pd);
        }
        foreach (var f in _svc.GetAllRootFolders() ?? new IEAPFolderEntity[0]) roots.Add(DumpFolder(f, 0, maxDepth, visited));
        foreach (var dr in _svc.GetAllRootDrawings() ?? new IEAPDrawingEntity[0]) roots.Add(DumpDrawing(dr, 0, maxDepth));
        return Ok(new Dictionary<string, object> { { "roots", roots }, { "user", _svc.CurSysUser != null ? _svc.CurSysUser.UserName : null } });
    }

    static Dictionary<string, object> CmdFind(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        string identity = Convert.ToString(a["identity"]);
        string kind = a.ContainsKey("kind") ? Convert.ToString(a["kind"]) : "all";
        var found = new List<object>();
        var visited = new HashSet<string>();
        if (kind == "product" || kind == "all")
        {
            foreach (var p in _svc.GetAllRootProducts() ?? new IEAPProductEntity[0])
            {
                if (string.Equals(p.IdentityNo, identity, StringComparison.OrdinalIgnoreCase)) found.Add(AttrDict(p));
            }
        }
        return Ok(new Dictionary<string, object> { { "found", found } });
    }

    static Dictionary<string, object> CmdListProducts()
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        var list = new List<object>();
        var seen = new HashSet<int>();
        Action<IEAPProductEntity> addProduct = p =>
        {
            if (seen.Add(p.ObjectID))
            {
                list.Add(new Dictionary<string, object>
                {
                    { "object_id", p.ObjectID },
                    { "class_id", p.ClassID },
                    { "identity", p.IdentityNo },
                    { "name", p.Name }
                });
            }
        };
        foreach (var p in _svc.GetAllRootProducts() ?? new IEAPProductEntity[0])
            addProduct(p);
        foreach (var pf in _svc.GetAllRootProductFolders() ?? new IEAPProductFolderEntity[0])
            CollectProducts(pf, addProduct);
        return Ok(new Dictionary<string, object> { { "products", list } });
    }

    static void CollectProducts(IEAPProductFolderEntity pf, Action<IEAPProductEntity> add)
    {
        if (pf == null) return;
        try
        {
            foreach (var prod in pf.RelatedProducts ?? new IEAPProductEntity[0]) add(prod);
            foreach (var sub in pf.Children ?? new IEAPProductFolderEntity[0]) CollectProducts(sub, add);
        }
        catch { }
    }

    static IEAPProductEntity FindProductRecursive(string keyword, out string path)
    {
        path = null;
        foreach (var p in _svc.GetAllRootProducts() ?? new IEAPProductEntity[0])
        {
            if (ProductMatches(p, keyword)) { path = "根"; return p; }
        }
        foreach (var pf in _svc.GetAllRootProductFolders() ?? new IEAPProductFolderEntity[0])
        {
            IEAPProductEntity found = null;
            string cur = FindInProductFolder(pf, keyword, out found);
            if (found != null) { path = cur; return found; }
        }
        return null;
    }

    static bool ProductMatches(IEAPProductEntity p, string keyword)
    {
        string kw = keyword.Trim();
        bool idOk = !string.IsNullOrEmpty(p.IdentityNo) &&
                    p.IdentityNo.IndexOf(kw, StringComparison.OrdinalIgnoreCase) >= 0;
        bool nameOk = !string.IsNullOrEmpty(p.Name) &&
                      p.Name.IndexOf(kw, StringComparison.OrdinalIgnoreCase) >= 0;
        return idOk || nameOk;
    }

    static string FindInProductFolder(IEAPProductFolderEntity pf, string keyword, out IEAPProductEntity found)
    {
        found = null;
        if (pf == null) return "";
        try
        {
            foreach (var prod in pf.RelatedProducts ?? new IEAPProductEntity[0])
            {
                if (ProductMatches(prod, keyword)) { found = prod; return pf.Name; }
            }
            foreach (var sub in pf.Children ?? new IEAPProductFolderEntity[0])
            {
                string subPath = FindInProductFolder(sub, keyword, out found);
                if (found != null) return (string.IsNullOrEmpty(pf.Name) ? "" : pf.Name + " / ") + subPath;
            }
        }
        catch { }
        return "";
    }

    static string NormalizeProductFolderPath(string value)
    {
        return string.Join("/",
            (value ?? "").Replace('\\', '/')
            .Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries)
            .Select(x => x.Trim())
            .Where(x => x.Length > 0));
    }

    static IEAPProductFolderEntity FindProductFolderRecursive(
        IEAPProductFolderEntity folder,
        string keyword,
        string parentPath,
        out string matchedPath)
    {
        matchedPath = null;
        if (folder == null) return null;
        string folderName = (folder.Name ?? "").Trim();
        string path = string.IsNullOrEmpty(parentPath)
            ? folderName
            : parentPath + "/" + folderName;
        string normalized = NormalizeProductFolderPath(keyword);
        bool exactPath = string.Equals(
            NormalizeProductFolderPath(path), normalized,
            StringComparison.OrdinalIgnoreCase);
        bool singleName = !normalized.Contains("/") &&
            (string.Equals(folderName, normalized, StringComparison.OrdinalIgnoreCase) ||
             string.Equals((folder.IdentityNo ?? "").Trim(), normalized,
                           StringComparison.OrdinalIgnoreCase));
        if (exactPath || singleName)
        {
            matchedPath = path;
            return folder;
        }
        try
        {
            foreach (var sub in folder.Children ?? new IEAPProductFolderEntity[0])
            {
                string hitPath = null;
                IEAPProductFolderEntity hit = FindProductFolderRecursive(
                    sub, keyword, path, out hitPath);
                if (hit != null)
                {
                    matchedPath = hitPath;
                    return hit;
                }
            }
        }
        catch { }
        return null;
    }

    static IEAPProductFolderEntity FindProductFolderRecursive(
        string keyword,
        out string matchedPath)
    {
        matchedPath = null;
        foreach (var root in _svc.GetAllRootProductFolders() ??
                 new IEAPProductFolderEntity[0])
        {
            string hitPath = null;
            IEAPProductFolderEntity hit = FindProductFolderRecursive(
                root, keyword, "", out hitPath);
            if (hit != null)
            {
                matchedPath = hitPath;
                return hit;
            }
        }
        return null;
    }

    static IEAPProductFolderEntity FindDirectChildProductFolder(
        IEAPProductFolderEntity parent,
        string nameOrIdentity)
    {
        if (parent == null) return null;
        try
        {
            foreach (var child in parent.Children ??
                     new IEAPProductFolderEntity[0])
            {
                if (child != null &&
                    (string.Equals((child.Name ?? "").Trim(),
                                   nameOrIdentity,
                                   StringComparison.OrdinalIgnoreCase) ||
                     string.Equals((child.IdentityNo ?? "").Trim(),
                                   nameOrIdentity,
                                   StringComparison.OrdinalIgnoreCase)))
                {
                    return child;
                }
            }
        }
        catch { }
        return null;
    }

    static IEAPProductFolderEntity FindRootProductFolder(
        string nameOrIdentity)
    {
        try
        {
            foreach (var folder in _svc.GetAllRootProductFolders() ??
                     new IEAPProductFolderEntity[0])
            {
                if (folder != null &&
                    (string.Equals((folder.Name ?? "").Trim(),
                                   nameOrIdentity,
                                   StringComparison.OrdinalIgnoreCase) ||
                     string.Equals((folder.IdentityNo ?? "").Trim(),
                                   nameOrIdentity,
                                   StringComparison.OrdinalIgnoreCase)))
                {
                    return folder;
                }
            }
        }
        catch { }
        return null;
    }

    static bool ProductFolderContainsChild(
        IEAPProductFolderEntity parent,
        int childId)
    {
        try
        {
            parent.Refresh();
            foreach (var child in parent.Children ??
                     new IEAPProductFolderEntity[0])
            {
                if (child != null && child.ObjectID == childId) return true;
            }
        }
        catch { }
        return false;
    }

    static IEAPProductFolderEntity EnsureProductFolderPath(
        string value,
        bool create,
        out bool createdAny,
        out List<string> createdPaths)
    {
        createdAny = false;
        createdPaths = new List<string>();
        string normalized = NormalizeProductFolderPath(value);
        if (string.IsNullOrEmpty(normalized)) return null;
        string[] parts = normalized.Split('/');
        IEAPProductFolderEntity parent = null;
        var resolved = new List<string>();
        foreach (string part in parts)
        {
            IEAPProductFolderEntity current = parent == null
                ? FindRootProductFolder(part)
                : FindDirectChildProductFolder(parent, part);
            if (current == null)
            {
                if (!create) return null;
                current = _svc.NewProductFolder();
                if (current == null) return null;
                try { current.Name = part; } catch { }
                try { current.IdentityNo = part; } catch { }
                try { current.Description = "由图纸上传助手创建"; } catch { }
                bool inserted = false;
                try { inserted = current.Insert(); } catch { }
                if (!inserted || current.ObjectID <= 0) return null;
                if (parent != null)
                {
                    bool linked = false;
                    try { linked = parent.LinkSubProductFolder(current); } catch { }
                    if (!linked ||
                        !ProductFolderContainsChild(parent, current.ObjectID))
                    {
                        return null;
                    }
                }
                createdAny = true;
                resolved.Add(part);
                createdPaths.Add(string.Join("/", resolved));
            }
            else
            {
                resolved.Add((current.Name ?? part).Trim());
            }
            parent = current;
        }
        return parent;
    }

    static Dictionary<string, object> ProductFolderResult(
        IEAPProductFolderEntity folder,
        string path,
        bool createdAny,
        List<string> createdPaths)
    {
        if (folder == null) return Err("product folder not found: " + path);
        return Ok(new Dictionary<string, object>
        {
            { "class_id", folder.ClassID },
            { "object_id", folder.ObjectID },
            { "identity", folder.IdentityNo },
            { "name", folder.Name },
            { "path", path },
            { "existing", !createdAny },
            { "created", createdAny },
            { "created_paths", createdPaths }
        });
    }

    static Dictionary<string, object> CmdEnsureProductFolder(
        Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        string path = NormalizeProductFolderPath(
            Convert.ToString(a["keyword"]));
        if (string.IsNullOrEmpty(path)) return Err("product folder is required");
        bool create = !a.ContainsKey("create") ||
                      Convert.ToBoolean(a["create"]);
        if (create)
        {
            return Err(
                "creating product folders is disabled; create the series folder in CAXA first: " +
                path);
        }
        bool createdAny = false;
        List<string> createdPaths = new List<string>();
        IEAPProductFolderEntity folder = EnsureProductFolderPath(
            path, create, out createdAny, out createdPaths);
        if (folder == null)
        {
            return Err("product folder not found: " + path);
        }
        string matchedPath = null;
        IEAPProductFolderEntity verified = FindProductFolderRecursive(
            path, out matchedPath);
        if (verified == null)
        {
            return Err("product folder could not be read back: " + path);
        }
        return ProductFolderResult(
            verified,
            string.IsNullOrEmpty(matchedPath) ? path : matchedPath,
            createdAny,
            createdPaths);
    }

    static Dictionary<string, object> CmdFindProductFolder(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        string keyword = Convert.ToString(a["keyword"]);
        string matchedPath = null;
        IEAPProductFolderEntity folder = FindProductFolderRecursive(
            keyword, out matchedPath);
        var found = new List<object>();
        if (folder != null)
        {
            found.Add(new Dictionary<string, object>
            {
                { "class_id", folder.ClassID },
                { "object_id", folder.ObjectID },
                { "identity", folder.IdentityNo },
                { "name", folder.Name },
                { "path", matchedPath }
            });
        }
        return Ok(new Dictionary<string, object> { { "found", found } });
    }

    static Dictionary<string, object> CmdLinkProductToFolder(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int productId = Convert.ToInt32(a["product_id"]);
        string folderPath = Convert.ToString(a["product_folder"]);
        IEAPProductEntity product = _svc.GetProduct(productId);
        if (product == null) return Err("product not found: " + productId);
        string matchedPath = null;
        IEAPProductFolderEntity folder = FindProductFolderRecursive(
            folderPath, out matchedPath);
        if (folder == null)
        {
            return Err("product folder not found: " + folderPath);
        }
        bool alreadyLinked = false;
        try
        {
            foreach (var linkedProduct in folder.RelatedProducts ??
                     new IEAPProductEntity[0])
            {
                if (linkedProduct != null &&
                    linkedProduct.ObjectID == product.ObjectID)
                {
                    alreadyLinked = true;
                    break;
                }
            }
        }
        catch { }
        if (!alreadyLinked)
        {
            bool ok = folder.LinkProduct(product);
            if (!ok)
            {
                return WriteError(
                    "LinkProduct returned false: " +
                    product.ObjectID + " -> " + folder.ObjectID,
                    "link_product_to_folder:link",
                    new Dictionary<string, object>
                    {
                        { "product", product.ObjectID },
                        { "product_folder", folder.ObjectID }
                    },
                    product.ObjectID);
            }
        }
        bool verified = false;
        try
        {
            foreach (var linkedProduct in folder.RelatedProducts ??
                     new IEAPProductEntity[0])
            {
                if (linkedProduct != null &&
                    linkedProduct.ObjectID == product.ObjectID)
                {
                    verified = true;
                    break;
                }
            }
        }
        catch { }
        if (!verified)
        {
            return WriteError(
                "product-folder link could not be read back: " +
                product.ObjectID + " -> " + folder.ObjectID,
                "link_product_to_folder:verify",
                new Dictionary<string, object>
                {
                    { "product", product.ObjectID },
                    { "product_folder", folder.ObjectID }
                },
                product.ObjectID);
        }
        try
        {
            return Ok(new Dictionary<string, object>
            {
                { "product_id", product.ObjectID },
                { "product_identity", product.IdentityNo },
                { "product_folder", matchedPath },
                { "product_folder_id", folder.ObjectID },
                { "already_linked", alreadyLinked }
            });
        }
        catch (Exception ex)
        {
            return WriteError(
                "product-folder link created but result readback failed: " +
                ex.Message,
                "link_product_to_folder:verify_result",
                new Dictionary<string, object>
                {
                    { "product", product.ObjectID },
                    { "product_folder", folder.ObjectID }
                },
                product.ObjectID,
                ex.ToString());
        }
    }

    static bool FolderHasProduct(IEAPProductFolderEntity folder, int productId)
    {
        folder.Refresh();
        foreach (var item in folder.RelatedProducts ?? new IEAPProductEntity[0])
        {
            if (item == null) throw new InvalidOperationException("unreadable folder product");
            if (item.ObjectID == productId) return true;
        }
        return false;
    }

    static Dictionary<string, object> CmdUnlinkProductFromFolder(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int productId = Convert.ToInt32(a["product_id"]);
        string folderPath = Convert.ToString(a["product_folder"]);
        IEAPProductEntity product = _svc.GetProduct(productId);
        if (product == null) return Err("product not found: " + productId);
        string matchedPath = null;
        IEAPProductFolderEntity folder = FindProductFolderRecursive(
            folderPath, out matchedPath);
        if (folder == null)
        {
            return Err("product folder not found: " + folderPath);
        }
        bool linked = FolderHasProduct(folder, productId);
        if (linked)
        {
            var ids = new Dictionary<string, object>
            {
                { "product", productId },
                { "product_folder", folder.ObjectID }
            };
            string stage = "unlink_product_from_folder:unlink";
            try
            {
                bool removed = folder.UnLinkProduct(product);
                stage = "unlink_product_from_folder:verify";
                if (FolderHasProduct(folder, productId))
                {
                    return WriteError(removed
                        ? "product-folder link still exists after unlink"
                        : "UnLinkProduct returned false and the link still exists",
                        stage, ids, productId);
                }
            }
            catch (Exception ex)
            {
                return WriteError("product-folder unlink could not be confirmed: " + ex.Message,
                    stage, ids, productId, ex.ToString());
            }
        }
        return Ok(new Dictionary<string, object>
        {
            { "product_id", productId },
            { "product_folder", matchedPath },
            { "was_linked", linked }
        });
    }

    static IEAPPrd2PartEntity FindProductRootLink(IEAPProductEntity product, int partId)
    {
        product.Refresh();
        bool incomplete = false;
        foreach (var link in product.RelatedRootParts((IEAPPrd2PartEntity)null) ??
                 new IEAPPrd2PartEntity[0])
        {
            IEAPPartEntity part = link == null ? null : link.RefPart;
            if (part == null || part.ObjectID <= 0)
            {
                incomplete = true;
                continue;
            }
            if (part.ObjectID == partId) return link;
        }
        if (incomplete) throw new InvalidOperationException("unreadable product-part endpoint");
        return null;
    }

    static Dictionary<string, object> CmdUnlinkProductPart(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int productId = Convert.ToInt32(a["product_id"]);
        int partId = Convert.ToInt32(a["part_id"]);
        IEAPProductEntity product = _svc.GetProduct(productId);
        IEAPPartEntity part = _svc.GetPart(partId);
        if (product == null || part == null) return Err("product/part not found");
        IEAPPrd2PartEntity match = FindProductRootLink(product, partId);
        if (match != null)
        {
            var ids = new Dictionary<string, object>
            {
                { "product", productId },
                { "part", partId },
                { "relation", match.ObjectID }
            };
            bool ok = false;
            try { ok = product.UnLinkPart(part, match); } catch { }
            if (!ok)
            {
                try { ok = product.UnLinkPart(part, null); } catch { }
            }
            if (!ok)
            {
                // Some CAXA 2011 builds expose the relation but return
                // “not implemented” for UnLinkPart. Delete only the verified
                // product-part relation object; never delete Product or Part.
                try
                {
                    _deleteDialogAutoConfirm = true;
                    try { match.Delete(); }
                    finally { _deleteDialogAutoConfirm = false; }
                    ok = true;
                }
                catch { }
            }

            const string stage = "unlink_product_part:verify";
            try
            {
                if (FindProductRootLink(product, partId) != null)
                {
                    return WriteError(ok
                        ? "product-part link still exists after unlink"
                        : "product-part relation could not be removed", stage, ids, partId);
                }
            }
            catch (Exception ex)
            {
                return WriteError("product-part unlink could not be confirmed: " + ex.Message,
                    stage, ids, partId, ex.ToString());
            }
        }
        return Ok(new Dictionary<string, object>
        {
            { "product_id", productId },
            { "part_id", partId },
            { "was_linked", match != null }
        });
    }

    static Dictionary<string, object> CmdUnlinkPartRelation(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        short linkClassId = Convert.ToInt16(a["link_class_id"]);
        int linkId = Convert.ToInt32(a["link_id"]);
        int parentId = Convert.ToInt32(a["parent_id"]);
        int childId = Convert.ToInt32(a["child_id"]);
        IEAPPartEntity parent = _svc.GetPart(parentId);
        if (parent == null) return Err("parent part not found: " + parentId);
        parent.Refresh();
        IEAPPart2PartEntity match = null;
        foreach (var link in parent.Children(null) ?? new IEAPPart2PartEntity[0])
        {
            if (link != null && link.ClassID == linkClassId &&
                link.ObjectID == linkId && ReadPart2PartChildId(link, parentId) == childId)
            {
                match = link;
                break;
            }
        }
        if (match == null)
        {
            return Err("part relation not found under parent: " + linkId);
        }
        var ids = new Dictionary<string, object>
        {
            { "parent", parentId },
            { "child", childId },
            { "relation", linkId }
        };
        _deleteDialogAutoConfirm = true;
        try { match.Delete(); }
        catch (Exception ex)
        {
            return WriteError("part relation delete failed: " + ex.Message,
                "unlink_part_relation:after_delete", ids, linkId, ex.ToString());
        }
        finally { _deleteDialogAutoConfirm = false; }

        const string stage = "unlink_part_relation:verify";
        try
        {
            parent.Refresh();
            foreach (var link in parent.Children(null) ?? new IEAPPart2PartEntity[0])
            {
                if (link == null)
                {
                    throw new InvalidOperationException("unreadable Part2Part relation");
                }
                if (link.ClassID == linkClassId && link.ObjectID == linkId)
                {
                    return WriteError("part relation still exists after delete", stage, ids, linkId);
                }
            }
        }
        catch (Exception ex)
        {
            return WriteError("part relation delete could not be confirmed: " + ex.Message,
                stage, ids, linkId, ex.ToString());
        }
        return Ok(new Dictionary<string, object>
        {
            { "link_class_id", linkClassId },
            { "link_id", linkId },
            { "parent_id", parentId },
            { "child_id", childId },
            { "deleted_relation_only", true }
        });
    }

    static List<IEAPProductEntity> GetAllProductsList()
    {
        var all = new List<IEAPProductEntity>();
        var seen = new HashSet<int>();
        Action<IEAPProductEntity> add = p =>
        {
            if (seen.Add(p.ObjectID)) all.Add(p);
        };
        foreach (var p in _svc.GetAllRootProducts() ?? new IEAPProductEntity[0]) add(p);
        foreach (var pf in _svc.GetAllRootProductFolders() ?? new IEAPProductFolderEntity[0]) CollectProducts(pf, add);
        return all;
    }

    static bool PartMatches(IEAPPartEntity pt, string keyword)
    {
        string kw = keyword.Trim();
        bool idOk = !string.IsNullOrEmpty(pt.IdentityNo) &&
                    pt.IdentityNo.IndexOf(kw, StringComparison.OrdinalIgnoreCase) >= 0;
        bool nameOk = !string.IsNullOrEmpty(pt.Name) &&
                      pt.Name.IndexOf(kw, StringComparison.OrdinalIgnoreCase) >= 0;
        return idOk || nameOk;
    }

    static void SearchPartRecursive(IEAPPartEntity pt, string keyword, List<Dictionary<string, object>> found,
                                    HashSet<string> visited, string path, int limit)
    {
        if (pt == null || found.Count >= limit) return;
        string key = "P" + pt.ClassID + ":" + pt.ObjectID;
        if (!visited.Add(key)) return;
        if (PartMatches(pt, keyword))
        {
            found.Add(new Dictionary<string, object>
            {
                { "part_id", pt.ObjectID },
                { "identity", pt.IdentityNo },
                { "name", pt.Name },
                { "is_borrowed", pt.IsBorrowed },
                { "path", path }
            });
            if (found.Count >= limit) return;
        }
        try
        {
            foreach (var l in pt.Children(null) ?? new IEAPPart2PartEntity[0])
            {
                if (l == null || l.RefSubPart == null) continue;
                SearchPartRecursive(l.RefSubPart, keyword, found, visited, path + " / " + pt.IdentityNo, limit);
            }
        }
        catch { }
    }

    static Dictionary<string, object> CmdFindPart(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        string keyword = Convert.ToString(a["keyword"]);
        int limit = a.ContainsKey("limit") ? Convert.ToInt32(a["limit"]) : 5;
        var found = new List<Dictionary<string, object>>();
        int productsScanned = 0;
        foreach (var prod in GetAllProductsList())
        {
            productsScanned++;
            try
            {
                foreach (var link in prod.RelatedRootParts((IEAPPrd2PartEntity)null) ?? new IEAPPrd2PartEntity[0])
                {
                    if (link == null || link.RefPart == null) continue;
                    SearchPartRecursive(link.RefPart, keyword, found, new HashSet<string>(),
                        "产品 " + (prod.IdentityNo ?? "") + " " + (prod.Name ?? ""), limit);
                    if (found.Count >= limit) break;
                }
            }
            catch { }
            if (found.Count >= limit) break;
        }
        return Ok(new Dictionary<string, object> { { "found", found }, { "products_scanned", productsScanned } });
    }

    static Dictionary<string, object> CmdPartInfo(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int id = Convert.ToInt32(a["part_id"]);
        IEAPPartEntity pt = _svc.GetPart(id);
        if (pt == null) return Err("part not found: " + id);
        string refreshError = null;
        try { pt.Refresh(); }
        catch (Exception ex) { refreshError = ex.Message; }
        var res = new Dictionary<string, object>
        {
            { "part_id", pt.ObjectID },
            { "identity", pt.IdentityNo },
            { "name", pt.Name },
            { "is_borrowed", pt.IsBorrowed }
        };
        if (refreshError != null) res["refresh_error"] = refreshError;
        var children = new List<object>();
        try
        {
            foreach (var l in pt.Children(null) ?? new IEAPPart2PartEntity[0])
            {
                var d = new Dictionary<string, object>
                {
                    { "sub_identity", l.RefSubPart != null ? l.RefSubPart.IdentityNo : null },
                    { "sub_name", l.RefSubPart != null ? l.RefSubPart.Name : null },
                    { "sub_id", l.RefSubPart != null ? (object)l.RefSubPart.ObjectID : null },
                    { "reference", l.Reference },
                    { "quantity", l.AssemblyQuantity },
                    { "serial", l.SerialNo }
                };
                if (l.RefSubPart == null)
                {
                    try { l.Refresh(); } catch { }
                    try
                    {
                        d["sub_identity2"] = l.RefSubPart != null ? l.RefSubPart.IdentityNo : null;
                        d["sub_name2"] = l.RefSubPart != null ? l.RefSubPart.Name : null;
                    }
                    catch { }
                    try
                    {
                        var props = l.GetType().GetProperties();
                        foreach (var prop in props)
                        {
                            try
                            {
                                object val = prop.GetValue(l, null);
                                d["prop_" + prop.Name] = val == null ? null : val.ToString();
                            }
                            catch { }
                        }
                    }
                    catch { }
                }
                children.Add(d);
            }
            res["children_count"] = children.Count;
        }
        catch (Exception ex)
        {
            res["children_error"] = ex.Message;
        }
        res["children"] = children;
        try
        {
            var parents = new List<object>();
            foreach (var l in pt.ParentParts(null) ?? new IEAPPart2PartEntity[0])
            {
                parents.Add(new Dictionary<string, object>
                {
                    { "parent_identity", l.RefParentPart != null ? l.RefParentPart.IdentityNo : null },
                    { "parent_name", l.RefParentPart != null ? l.RefParentPart.Name : null },
                    { "parent_id", l.RefParentPart != null ? (object)l.RefParentPart.ObjectID : null },
                    { "reference", l.Reference }
                });
            }
            res["parents"] = parents;
        }
        catch (Exception ex)
        {
            res["parents_error"] = ex.Message;
        }
        try
        {
            var prods = new List<object>();
            foreach (var l in pt.ParentProducts(null) ?? new IEAPPrd2PartEntity[0])
            {
                prods.Add(new Dictionary<string, object>
                {
                    { "product_identity", l.RefProduct != null ? l.RefProduct.IdentityNo : null },
                    { "product_name", l.RefProduct != null ? l.RefProduct.Name : null },
                    { "product_id", l.RefProduct != null ? (object)l.RefProduct.ObjectID : null },
                    { "reference", l.Reference }
                });
            }
            res["products"] = prods;
        }
        catch (Exception ex)
        {
            res["products_error"] = ex.Message;
        }
        try
        {
            var linked = new List<object>();
            foreach (var ent in _svc.RetrieveAllLinksOfObject((short)1019, id) ?? new IEAPEntityBase[0])
            {
                var d = new Dictionary<string, object> { { "class", ent.GetType().Name }, { "object_id", ent.ObjectID } };
                string ident = null, nm = null;
                try { ident = ent.GetValueAsString("IdentityNo"); } catch { }
                try { nm = ent.GetValueAsString("name"); } catch { }
                if (string.IsNullOrEmpty(ident))
                {
                    IEAPPartEntity lp = ent as IEAPPartEntity;
                    if (lp != null) ident = lp.IdentityNo;
                    IEAPDrawingEntity ld = ent as IEAPDrawingEntity;
                    if (ld != null) ident = ld.DrawNo;
                }
                d["identity"] = ident;
                d["name"] = nm;
                linked.Add(d);
            }
            res["all_linked_objects"] = linked;
        }
        catch (Exception ex)
        {
            res["all_linked_error"] = ex.Message;
        }
        var linkEndpoints = new List<object>();
        try
        {
            var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
            var session = engine != null ? engine.CurSession : null;
            if (session != null)
            {
                var store = session.ObjectStore;
                foreach (var l in pt.Children(null) ?? new IEAPPart2PartEntity[0])
                {
                    var ep = new Dictionary<string, object> { { "reference", l.Reference }, { "quantity", l.AssemblyQuantity } };
                    try { ep["link_class"] = l.ClassID; } catch { }
                    try { ep["link_id"] = l.ObjectID; } catch { }
                    try
                    {
                        var lo = store.RetrieveObject((short)Convert.ToInt32(ep["link_class"]),
                                                      Convert.ToInt32(ep["link_id"]))
                                   as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxLinkObject;
                        if (lo != null)
                        {
                            AddEndpoint(ep, "A", lo.RoleAObject);
                            AddEndpoint(ep, "B", lo.RoleBObject);
                        }
                        else
                        {
                            ep["link_error"] = "not ICaxLinkObject";
                        }
                    }
                    catch (Exception ex)
                    {
                        ep["link_error"] = ex.Message;
                    }
                    linkEndpoints.Add(ep);
                }
            }
            else
            {
                res["link_endpoints_session"] = "unavailable";
            }
        }
        catch (Exception ex)
        {
            res["link_endpoints_error"] = ex.Message;
        }
        res["link_endpoints"] = linkEndpoints;
        try
        {
            var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
            var session = engine != null ? engine.CurSession : null;
            if (session == null)
            {
                res["lowlevel"] = "session unavailable";
            }
            else
            {
                var store = session.ObjectStore;
                var obj = store.RetrieveObject((short)1019, id) as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
                res["lowlevel_obj"] = obj != null ? "ok" : "null";
                if (obj == null)
                {
                    res["lowlevel"] = "obj null";
                }
                else
                {
                    res["lowlevel_direct_children"] = SafeEnumerate("direct_children", () => TryEnumerate(obj.RetrieveDirectChildren("")));
                    res["lowlevel_relations"] = SafeEnumerate("relations", () => TryEnumerate(obj.RetrieveRelations(null)));
                    res["lowlevel_roleb_relations"] = SafeEnumerate("roleb", () => TryEnumerate(obj.RetrieveRoleBRelations(null)));
                    res["lowlevel_all_links"] = SafeEnumerate("all_links", () => TryEnumerate(obj.RetrieveAllLinks()));
                }
            }
        }
        catch (Exception ex)
        {
            res["lowlevel_error"] = ex.Message;
        }
        return Ok(res);
    }

    static void AddEndpoint(Dictionary<string, object> ep, string tag,
                            CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject obj)
    {
        var d = new Dictionary<string, object>();
        if (obj == null)
        {
            ep["role" + tag] = null;
            return;
        }
        var ob = obj as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObjectBase;
        if (ob != null)
        {
            try { d["class"] = ob.ClassName; } catch { }
            try { d["object_id"] = ob.ObjectId; } catch { }
            try { d["identity"] = ob.GetValueAsString("IdentityNo"); } catch { }
            try { d["name"] = ob.GetValueAsString("name"); } catch { }
        }
        ep["role" + tag] = d;
    }

    static object SafeEnumerate(string label, Func<object> fn)
    {
        try
        {
            return fn();
        }
        catch (Exception ex)
        {
            return label + " error: " + ex.Message;
        }
    }

    static object TryEnumerate(System.Collections.IEnumerable en)
    {
        var list = new List<object>();
        try
        {
            if (en == null) return "null";
            foreach (object o in en)
            {
                if (o == null) { list.Add(null); continue; }
                var d = new Dictionary<string, object> { { "type", o.GetType().Name } };
                var ob = o as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObjectBase;
                if (ob != null)
                {
                    d["class"] = ob.ClassName;
                    d["object_id"] = ob.ObjectId;
                    try { d["identity"] = ob.GetValueAsString("IdentityNo"); } catch { }
                    try { d["name"] = ob.GetValueAsString("name"); } catch { }
                }
                list.Add(d);
            }
            return list;
        }
        catch (Exception ex)
        {
            return "error: " + ex.Message;
        }
    }

    static Dictionary<string, object> CmdFindPartInProduct(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int productId = Convert.ToInt32(a["product_id"]);
        string keyword = Convert.ToString(a["keyword"]);
        int limit = a.ContainsKey("limit") ? Convert.ToInt32(a["limit"]) : 20;
        IEAPProductEntity prod = _svc.GetProduct(productId);
        if (prod == null) return Err("product not found: " + productId);
        var found = new List<Dictionary<string, object>>();
        try
        {
            foreach (var link in prod.RelatedRootParts((IEAPPrd2PartEntity)null) ?? new IEAPPrd2PartEntity[0])
            {
                if (link == null || link.RefPart == null) continue;
                SearchPartRecursive(link.RefPart, keyword, found, new HashSet<string>(),
                    "产品 " + (prod.IdentityNo ?? "") + " " + (prod.Name ?? ""), limit);
                if (found.Count >= limit) break;
            }
        }
        catch { }
        return Ok(new Dictionary<string, object> { { "found", found } });
    }

    static Dictionary<string, object> CmdQueryParts(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        string keyword = Convert.ToString(a["keyword"]);
        var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
        var session = engine != null ? engine.CurSession : null;
        if (session == null) return Err("session unavailable");
        try
        {
            var store = session.ObjectStore;
            var q = store.NewQuery();
            var cond = q.QueryCondition;
            cond.QueriedClassID = 1019;
            var where = store.NewQueryWhere();
            where.PropertyName = "IdentityNo";
            where.OperatorType = CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.EnumWhereOperatorTypes.OP_Contain;
            where.OperatorValue = keyword;
            cond.AddWhere(where);
            bool ok = q.Run();
            var qr = q.QueryResult;
            var items = TryEnumerate(qr.GetAllObjectBases() as System.Collections.IEnumerable);
            return Ok(new Dictionary<string, object> { { "run", ok }, { "count", qr.Count }, { "items", items } });
        }
        catch (Exception ex)
        {
            return Err("query error: " + ex.Message);
        }
    }

    // Read-only generic object query for diagnostics and metadata inspection.
    static Dictionary<string, object> CmdQueryObjects(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        short classId = Convert.ToInt16(a["class_id"]);
        int limit = a.ContainsKey("limit") ? Convert.ToInt32(a["limit"]) : 100;
        var propertyNames = new List<string>();
        if (a.ContainsKey("properties") && a["properties"] != null)
        {
            var values = a["properties"] as System.Collections.IEnumerable;
            if (values != null)
            {
                foreach (object value in values)
                {
                    if (value != null) propertyNames.Add(Convert.ToString(value));
                }
            }
        }
        var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
        var session = engine != null ? engine.CurSession : null;
        if (session == null) return Err("session unavailable");
        try
        {
            var store = session.ObjectStore;
            var query = store.NewQuery();
            var condition = query.QueryCondition;
            condition.QueriedClassID = classId;
            if (a.ContainsKey("where_property") && a["where_property"] != null &&
                a.ContainsKey("where_value") && a["where_value"] != null)
            {
                var where = store.NewQueryWhere();
                where.PropertyName = Convert.ToString(a["where_property"]);
                string op = a.ContainsKey("where_operator")
                    ? Convert.ToString(a["where_operator"]).ToLowerInvariant()
                    : "contain";
                switch (op)
                {
                    case "equal":
                        where.OperatorType = CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.EnumWhereOperatorTypes.OP_Equal;
                        break;
                    case "not_equal":
                        where.OperatorType = CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.EnumWhereOperatorTypes.OP_NotEqual;
                        break;
                    case "prefix":
                        where.OperatorType = CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.EnumWhereOperatorTypes.OP_PreContain;
                        break;
                    case "suffix":
                        where.OperatorType = CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.EnumWhereOperatorTypes.OP_PostContain;
                        break;
                    default:
                        where.OperatorType = CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.EnumWhereOperatorTypes.OP_Contain;
                        break;
                }
                where.OperatorValue = Convert.ToString(a["where_value"]);
                condition.AddWhere(where);
            }
            bool run = query.Run();
            var items = new List<object>();
            foreach (object raw in query.QueryResult.GetAllObjectBases() as System.Collections.IEnumerable)
            {
                var obj = raw as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObjectBase;
                if (obj == null) continue;
                var item = new Dictionary<string, object>
                {
                    { "object_id", obj.ObjectId },
                    { "class_name", obj.ClassName }
                };
                foreach (string propertyName in propertyNames)
                {
                    try
                    {
                        item[propertyName] = obj.GetValueAsString(propertyName);
                    }
                    catch (Exception ex)
                    {
                        item[propertyName] = "ERROR: " + ex.Message;
                    }
                }
                items.Add(item);
                if (items.Count >= limit) break;
            }
            return Ok(new Dictionary<string, object>
            {
                { "run", run },
                { "count", query.QueryResult.Count },
                { "items", items }
            });
        }
        catch (Exception ex)
        {
            return Err("query_objects error: " + ex.Message);
        }
    }

    static Dictionary<string, object> CmdQueryLinks(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        short classId = Convert.ToInt16(a["class_id"]);
        int limit = a.ContainsKey("limit") ? Convert.ToInt32(a["limit"]) : 100;
        var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
        var session = engine != null ? engine.CurSession : null;
        if (session == null) return Err("session unavailable");
        try
        {
            var store = session.ObjectStore;
            var query = store.NewQuery();
            query.QueryCondition.QueriedClassID = classId;
            bool run = query.Run();
            var items = new List<object>();
            foreach (object raw in query.QueryResult.GetAllObjectBases() as System.Collections.IEnumerable)
            {
                var link = raw as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxLinkObject;
                if (link == null) continue;
                var item = new Dictionary<string, object>
                {
                    { "link_class_id", link.RefLinkClass != null ? link.RefLinkClass.ClassId : 0 },
                    { "link_object_id", link.ObjectId }
                };
                foreach (var pair in new[]
                {
                    new { Name = "roleA", Obj = link.RoleAObject },
                    new { Name = "roleB", Obj = link.RoleBObject }
                })
                {
                    if (pair.Obj == null)
                    {
                        item[pair.Name] = null;
                        continue;
                    }
                    var endpoint = new Dictionary<string, object>
                    {
                        { "class_name", pair.Obj.ClassName },
                        { "object_id", pair.Obj.ObjectId }
                    };
                    try { endpoint["identity"] = pair.Obj.GetValueAsString("IdentityNo"); } catch { }
                    try { endpoint["name"] = pair.Obj.GetValueAsString("name"); } catch { }
                    item[pair.Name] = endpoint;
                }
                items.Add(item);
                if (items.Count >= limit) break;
            }
            return Ok(new Dictionary<string, object>
            {
                { "run", run },
                { "count", query.QueryResult.Count },
                { "items", items }
            });
        }
        catch (Exception ex)
        {
            return Err("query_links error: " + ex.Message);
        }
    }

    static Dictionary<string, object> CmdWorkflowDalProbe(Dictionary<string, object> a)
    {
        var result = new Dictionary<string, object>();
        try
        {
            var process = new Interop.EAPLib.CxDalWfProcessClass();
            result["process_type"] = process.GetType().FullName;
            result["process_connection_id"] = process.connection_id;
            result["process_server_type"] = process.server_type;
            result["process_retry_connection_number"] = process.retry_connection_number;
            try
            {
                result["process_server_info"] = process.server_info != null
                    ? process.server_info.GetType().FullName
                    : null;
            }
            catch (Exception ex) { result["process_server_info_error"] = ex.Message; }
        }
        catch (Exception ex)
        {
            result["process_error"] = ex.ToString();
        }
        try
        {
            var connection = new Interop.EAPLib.CxDalWfConnectionClass();
            result["connection_type"] = connection.GetType().FullName;
            result["connection_connection_id"] = connection.connection_id;
            result["connection_server_type"] = connection.server_type;
            result["connection_retry_connection_number"] = connection.retry_connection_number;
        }
        catch (Exception ex)
        {
            result["connection_error"] = ex.ToString();
        }
        return Ok(result);
    }

    static Dictionary<string, object> CmdWorkflowConnectionProbe(Dictionary<string, object> a)
    {
        if (!a.ContainsKey("server_ip") || !a.ContainsKey("server_port") ||
            !a.ContainsKey("user_id"))
            return Err("server_ip, server_port, user_id are required");
        var result = new Dictionary<string, object>();
        Interop.EAPLib.CxDalWfConnectionClass connection = null;
        try
        {
            var serverInfo = new Interop.EAPLib.CxServerInfoClass
            {
                server_ip = Convert.ToString(a["server_ip"]),
                server_port = Convert.ToUInt32(a["server_port"]),
                server_name = a.ContainsKey("server_name")
                    ? Convert.ToString(a["server_name"])
                    : "Workflow"
            };
            connection = new Interop.EAPLib.CxDalWfConnectionClass();
            connection.server_type = (int)Interop.EAPLib.PLMSERVERTYPE.PLM_SERVERTYPE_WORKFLOWSERVER;
            connection.server_info = serverInfo;
            uint connectionId = connection.connect(Convert.ToUInt32(a["user_id"]));
            connection.connection_id = connectionId;
            result["server_ip"] = serverInfo.server_ip;
            result["server_port"] = serverInfo.server_port;
            result["connection_id"] = connectionId;
            result["server_type"] = connection.server_type;
            return Ok(result);
        }
        catch (Exception ex)
        {
            result["error"] = ex.ToString();
            return Ok(result);
        }
        finally
        {
            if (connection != null)
            {
                try
                {
                    if (connection.connection_id != 0)
                        connection.disconnect(connection.connection_id);
                }
                catch { }
            }
        }
    }

    static Dictionary<string, object> CmdRelatedObjects(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        short classId = Convert.ToInt16(a["class_id"]);
        int objectId = Convert.ToInt32(a["object_id"]);
        string roleName = a.ContainsKey("role_name")
            ? Convert.ToString(a["role_name"])
            : "";
        var propertyNames = new List<string>();
        if (a.ContainsKey("properties") && a["properties"] != null)
        {
            var values = a["properties"] as System.Collections.IEnumerable;
            if (values != null)
            {
                foreach (object value in values)
                    if (value != null) propertyNames.Add(Convert.ToString(value));
            }
        }
        var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
        var session = engine != null ? engine.CurSession : null;
        if (session == null) return Err("session unavailable");
        try
        {
            var store = session.ObjectStore;
            var obj = store.RetrieveObject(classId, objectId)
                as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
            if (obj == null) return Err("source object not found");
            var items = new List<object>();
            var related = obj.RetrieveDirectChildren(roleName) as System.Collections.IEnumerable;
            if (related != null)
            {
                foreach (object raw in related)
                {
                    var relObj = raw as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObjectBase;
                    if (relObj == null) continue;
                    var item = new Dictionary<string, object>
                    {
                        { "class_name", relObj.ClassName },
                        { "object_id", relObj.ObjectId }
                    };
                    foreach (string propertyName in propertyNames)
                    {
                        try { item[propertyName] = relObj.GetValueAsString(propertyName); }
                        catch (Exception ex) { item[propertyName] = "ERROR: " + ex.Message; }
                    }
                    items.Add(item);
                }
            }
            return Ok(new Dictionary<string, object>
            {
                { "source_class", obj.ClassName },
                { "source_id", obj.ObjectId },
                { "role_name", roleName },
                { "items", items }
            });
        }
        catch (Exception ex)
        {
            return Err("related_objects error: " + ex.ToString());
        }
    }

    static string GetClassNameByID(CAXA.A5.EAP.CommonLibrary.Session.ICaxSession session, short classId)
    {
        try
        {
            var cls = session.MetaInfo.GetClassByID(classId);
            return cls != null ? cls.ClassName : null;
        }
        catch
        {
            return null;
        }
    }

    static Dictionary<string, object> CmdClassInfo(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
        var session = engine != null ? engine.CurSession : null;
        if (session == null) return Err("session unavailable");
        var ids = new List<short>();
        if (a.ContainsKey("class_ids") && a["class_ids"] != null)
        {
            var arr = a["class_ids"] as System.Collections.IEnumerable;
            if (arr != null)
            {
                foreach (object o in arr) ids.Add(Convert.ToInt16(o));
            }
        }
        if (ids.Count == 0) ids.AddRange(new short[] { 1002, 1003, 1001 });
        var list = new List<object>();
        foreach (var id in ids)
        {
            try
            {
                var cls = session.MetaInfo.GetClassByID(id);
                list.Add(new Dictionary<string, object>
                {
                    { "id", id },
                    { "name", cls != null ? cls.ClassName : null },
                    { "ext", cls != null ? cls.ExternalName : null }
                });
            }
            catch (Exception ex)
            {
                list.Add(new Dictionary<string, object> { { "id", id }, { "err", ex.Message } });
            }
        }
        return Ok(new Dictionary<string, object> { { "classes", list } });
    }

    static Dictionary<string, object> CmdClassAttributes(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        short classId = Convert.ToInt16(a["class_id"]);
        try
        {
            var attributes = _svc.GetAttrsByClassID(classId);
            return Ok(new Dictionary<string, object>
            {
                { "class_id", classId },
                { "keys", attributes != null ? attributes.Keys : new string[0] }
            });
        }
        catch (Exception ex)
        {
            return Err("class attributes error: " + ex.Message);
        }
    }

    static Dictionary<string, object> CmdDumpProduct(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        string identity = Convert.ToString(a["identity"]);
        int maxDepth = a.ContainsKey("max_depth") ? Convert.ToInt32(a["max_depth"]) : 6;
        string path;
        IEAPProductEntity target = FindProductRecursive(identity, out path);
        if (target != null)
        {
            var visited = new HashSet<string>();
            return Ok(new Dictionary<string, object> { { "product", DumpProduct(target, 0, maxDepth, visited) }, { "path", path } });
        }
        return Err("product not found: " + identity);
    }

    static Dictionary<string, object> CmdDumpProductById(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int productId = Convert.ToInt32(a["product_id"]);
        IEAPProductEntity product = _svc.GetProduct(productId);
        if (product == null) return Err("product not found: " + productId);
        return Ok(new Dictionary<string, object>
        {
            { "product", DumpProduct(product, 0, 1, new HashSet<string>()) }
        });
    }

    static Dictionary<string, object> CmdFindProduct(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        string keyword = Convert.ToString(a["keyword"]);
        int limit = a.ContainsKey("limit") ? Convert.ToInt32(a["limit"]) : 20;
        var list = new List<object>();
        foreach (var p in GetAllProductsList())
        {
            if (!ProductMatches(p, keyword)) continue;
            list.Add(new Dictionary<string, object>
            {
                { "object_id", p.ObjectID },
                { "identity", p.IdentityNo },
                { "name", p.Name }
            });
            if (list.Count >= limit) break;
        }
        return Ok(new Dictionary<string, object> { { "found", list } });
    }

    static Dictionary<string, object> CmdProductRootPart(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int productId = Convert.ToInt32(a["product_id"]);
        string identity = Convert.ToString(a["identity"]);
        IEAPProductEntity prod = _svc.GetProduct(productId);
        if (prod == null) return Err("product not found");
        try
        {
            foreach (var link in prod.RelatedRootParts((IEAPPrd2PartEntity)null) ?? new IEAPPrd2PartEntity[0])
            {
                if (link == null || link.RefPart == null) continue;
                if (string.Equals(link.RefPart.IdentityNo, identity, StringComparison.OrdinalIgnoreCase))
                {
                    return Ok(new Dictionary<string, object>
                    {
                        { "part_id", link.RefPart.ObjectID },
                        { "identity", link.RefPart.IdentityNo },
                        { "name", link.RefPart.Name }
                    });
                }
            }
        }
        catch (Exception ex)
        {
            return Err("product root part error: " + ex.Message);
        }
        return Err("part not found under product");
    }

    static Dictionary<string, object> CmdCreatePart(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        string identity = Convert.ToString(a["identity_no"]);
        string name = a.ContainsKey("name") ? Convert.ToString(a["name"]) : "";
        IEAPPartEntity pt = _svc.NewPart(identity);
        if (pt == null) return Err("NewPart returned null");
        if (!string.IsNullOrEmpty(name)) { try { pt.Name = name; } catch { } }
        try { pt.MaterialNo = identity; } catch { }
        bool inserted = false;
        try
        {
            pt.Insert();
            inserted = true;
            int objectId = pt.ObjectID;
            return Ok(new Dictionary<string, object>
            {
                { "object_id", objectId },
                { "identity", pt.IdentityNo }
            });
        }
        catch (Exception ex)
        {
            int objectId = 0;
            try { objectId = pt.ObjectID; } catch { }
            var created = new Dictionary<string, object>();
            if (objectId > 0) created["part"] = objectId;
            return WriteError(
                "create_part failed after NewPart/Insert: " + ex.Message,
                inserted ? "create_part:after_insert" : "create_part:insert",
                created,
                objectId,
                ex.ToString());
        }
    }

    static Dictionary<string, object> CmdEnsurePartMaterialCode(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int partId = Convert.ToInt32(a["part_id"]);
        string identity = Convert.ToString(a["identity"]).Trim();
        if (string.IsNullOrEmpty(identity)) return Err("identity is required");
        IEAPPartEntity part = _svc.GetPart(partId);
        if (part == null) return Err("part not found: " + partId);
        string before = (part.MaterialNo ?? "").Trim();
        bool changed = false;
        if (!string.Equals(before, identity, StringComparison.OrdinalIgnoreCase))
        {
            try
            {
                part.MaterialNo = identity;
                part.Update();
                changed = true;
            }
            catch (Exception ex)
            {
                return WriteError(
                    "part material code update failed: " + ex.Message,
                    "ensure_part_material_code:after_update",
                    new Dictionary<string, object> { { "part", partId } },
                    partId,
                    ex.ToString());
            }
        }
        string verified = "";
        try
        {
            IEAPPartEntity after = _svc.GetPart(partId);
            verified = (after != null ? after.MaterialNo : "").Trim();
        }
        catch (Exception ex)
        {
            if (changed)
            {
                return WriteError(
                    "part material code readback failed: " + ex.Message,
                    "ensure_part_material_code:after_update",
                    new Dictionary<string, object> { { "part", partId } },
                    partId,
                    ex.ToString());
            }
            return Err("part material code readback failed: " + ex.Message);
        }
        if (!string.Equals(verified, identity, StringComparison.OrdinalIgnoreCase))
        {
            if (changed)
            {
                return WriteError(
                    "part material code could not be verified: " + verified,
                    "ensure_part_material_code:verify",
                    new Dictionary<string, object> { { "part", partId } },
                    partId);
            }
            return Err("part material code could not be verified: " + verified);
        }
        return Ok(new Dictionary<string, object>
        {
            { "part_id", partId },
            { "identity", identity },
            { "material_no", verified },
            { "changed", changed }
        });
    }

    static Dictionary<string, object> CmdCreateProduct(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        string identity = Convert.ToString(a["identity_no"]);
        string name = a.ContainsKey("name") ? Convert.ToString(a["name"]) : "";
        string productFolder = a.ContainsKey("product_folder")
            ? Convert.ToString(a["product_folder"])
            : "";
        if (string.IsNullOrWhiteSpace(productFolder))
        {
            return Err(
                "product folder is required; select an existing series folder in CAXA first");
        }
        IEAPProductFolderEntity folder = null;
        string folderPath = null;
        bool folderCreated = false;
        List<string> createdFolders = new List<string>();
        if (!string.IsNullOrWhiteSpace(productFolder))
        {
            folder = FindProductFolderRecursive(productFolder, out folderPath);
            if (folder == null)
            {
                return Err(
                    "product folder does not exist; create the series folder in CAXA first: " +
                    productFolder);
            }
        }
        IEAPProductEntity prod = _svc.NewProduct(identity);
        if (prod == null) return Err("NewProduct returned null");
        if (!string.IsNullOrEmpty(name)) { try { prod.Name = name; } catch { } }
        bool productInserted = false;
        int productId = 0;
        try
        {
            prod.Insert();
            productInserted = true;
            productId = prod.ObjectID;
        }
        catch (Exception ex)
        {
            try { productId = prod.ObjectID; } catch { }
            var created = new Dictionary<string, object>();
            if (productId > 0) created["product"] = productId;
            return WriteError(
                "create_product failed after NewProduct/Insert: " + ex.Message,
                productInserted
                    ? "create_product:after_insert"
                    : "create_product:insert",
                created,
                productId,
                ex.ToString());
        }
        bool linked = true;
        if (folder != null)
        {
            try
            {
                linked = folder.LinkProduct(prod);
            }
            catch (Exception ex)
            {
                return WriteError(
                    "product created but product-folder link failed: " + ex.Message,
                    "create_product:link_folder",
                    new Dictionary<string, object>
                    {
                        { "product", productId },
                        { "product_folder", folder.ObjectID }
                    },
                    productId,
                    ex.ToString());
            }
            if (!linked)
            {
                return WriteError(
                    "product created but product-folder link returned false: " +
                    productId + " -> " + folder.ObjectID,
                    "create_product:link_folder",
                    new Dictionary<string, object>
                    {
                        { "product", productId },
                        { "product_folder", folder.ObjectID }
                    },
                    productId);
            }
            bool verified = false;
            try
            {
                foreach (var linkedProduct in folder.RelatedProducts ??
                         new IEAPProductEntity[0])
                {
                    if (linkedProduct != null &&
                        linkedProduct.ObjectID == prod.ObjectID)
                    {
                        verified = true;
                        break;
                    }
                }
            }
            catch { }
            if (!verified)
            {
                return WriteError(
                    "product-folder link could not be read back: " +
                    productId + " -> " + folder.ObjectID,
                    "create_product:verify_folder_link",
                    new Dictionary<string, object>
                    {
                        { "product", productId },
                        { "product_folder", folder.ObjectID }
                    },
                    productId);
            }
        }
        try
        {
            return Ok(new Dictionary<string, object>
            {
                { "object_id", productId },
                { "identity", prod.IdentityNo },
                { "name", prod.Name },
                { "product_folder", folderPath ?? "" },
                { "product_folder_id", folder != null ? (object)folder.ObjectID : null },
                { "product_folder_created", folderCreated },
                { "created_product_folders", createdFolders },
                { "folder_linked", linked }
            });
        }
        catch (Exception ex)
        {
            return WriteError(
                "product created but result readback failed: " + ex.Message,
                "create_product:verify_result",
                new Dictionary<string, object> { { "product", productId } },
                productId,
                ex.ToString());
        }
    }

    static Dictionary<string, object> CmdReference(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int target = Convert.ToInt32(a["target_id"]);
        int source = Convert.ToInt32(a["source_id"]);
        string kind = Convert.ToString(a["kind"]);
        if (kind == "part_to_product")
        {
            IEAPProductEntity prod = _svc.GetProduct(target);
            IEAPPartEntity part = _svc.GetPart(source);
            if (prod == null || part == null) return Err("product or part not found");
            bool ok = prod.ReferenceToMe(part);
            return ok ? Ok() : Err("ReferenceToMe returned false");
        }
        return Err("unsupported reference kind: " + kind);
    }

    static short ClassIdByName(string name)
    {
        string n = (name ?? "").Trim().ToLowerInvariant();
        if (n == "drawing") return 1002;
        if (n == "cappdocument") return 1003;
        if (n == "document") return 1001;
        return 0;
    }

    // 上传期间只允许本 helper 进程自己的导入对话框被自动确认。
    static volatile bool _loginInProgress = false;
    static volatile bool _uploadInProgress = false;
    static volatile int _uploadCurrentObjectId = 0;

    static Dictionary<string, object> CmdUpload(Dictionary<string, object> a)
    {
        // Object evidence belongs to this command, never to the previous PDF.
        _uploadCurrentObjectId = 0;
        _uploadInProgress = true;
        try
        {
            return CmdUploadCore(a);
        }
        catch (Exception ex)
        {
            // 无法证明没有写入：返回结构化“部分/待核实”，上层不得盲目重放。
            var failed = Err("upload failed: " + ex.Message);
            failed["stage"] = "upload_unhandled";
            failed["detail"] = ex.ToString();
            failed["partial"] = true;
            failed["uncertain"] = true;
            if (_uploadCurrentObjectId != 0) failed["object_id"] = _uploadCurrentObjectId;
            return failed;
        }
        finally
        {
            _uploadInProgress = false;
        }
    }

    static Dictionary<string, object> CmdUploadCore(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        string parentKind = Convert.ToString(a["parent_kind"]); // product | part | folder
        int parentId = Convert.ToInt32(a["parent_id"]);
        int drawingType = a.ContainsKey("drawing_type") ? Convert.ToInt32(a["drawing_type"]) : (int)DrawingType.Document;
        string filePath = Convert.ToString(a["file_path"]);
        string drawNo = a.ContainsKey("draw_no") ? Convert.ToString(a["draw_no"]) : "";
        string name = a.ContainsKey("name") ? Convert.ToString(a["name"]) : "";
        string onDuplicate = a.ContainsKey("on_duplicate") ? Convert.ToString(a["on_duplicate"]) : "reuse";
        if (!File.Exists(filePath)) return Err("file not found: " + filePath);
        IEAPDrawingEntity dr = null;
        if (parentKind == "product")
        {
            IEAPProductEntity prod = _svc.GetProduct(parentId);
            if (prod == null) return Err("product not found");
            dr = prod.AttachFile((DrawingType)drawingType, filePath);
        }
        else if (parentKind == "folder")
        {
            IEAPFolderEntity folder = FindFolderById(parentId);
            if (folder == null) return Err("folder not found");
            dr = folder.AttachFile((DrawingType)drawingType, filePath, false);
        }
        else if (parentKind == "part")
        {
            IEAPPartEntity part = _svc.GetPart(parentId);
            if (part == null) return Err("part not found");
            var diag = new Dictionary<string, object>();
            CAXA.A5.EAP.CommonLibrary.Session.ICaxSession sess = null;
            try
            {
                var eng = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
                sess = eng != null ? eng.CurSession : null;
            }
            catch { }
            string attachMethod = null;
            IEAPDrawingEntity existingDuplicate = null;
            // 防重：零件下已有同名文档则复用（或按配置新增版本），不再重复挂接；
            // on_duplicate == "new_version" 时也按图号（DrawNo）命中，名称被识别变体也能命中。
            // append_iteration 则故意继续走导入，让 CAXA 的重复对象对话框出现，
            // 由后台自动确认线程选择“在现有版本上追加新的版次”。
            try
            {
                foreach (var comp in part.GetAllMaterDrawing() ?? new CompositeEntity[0])
                {
                    IEAPDrawingEntity de = comp.Entity as IEAPDrawingEntity;
                    if (de == null) continue;
                    bool nameHit = !string.IsNullOrEmpty(name) &&
                        string.Equals(de.Name, name, StringComparison.OrdinalIgnoreCase);
                    bool drawNoHit = onDuplicate == "new_version" && !string.IsNullOrEmpty(drawNo) &&
                        string.Equals(de.DrawNo, drawNo, StringComparison.OrdinalIgnoreCase);
                    if (nameHit || drawNoHit)
                    {
                        diag["exists"] = de.ObjectID;
                        try { diag["class_name"] = GetClassNameByID(sess, de.ClassID); } catch { }
                        diag["match"] = nameHit ? "name" : "draw_no";
                        if (onDuplicate == "append_iteration")
                        {
                            string existingState = "";
                            try { existingState = de.StateExt ?? ""; } catch { }
                            diag["existing_state"] = existingState;
                            if (existingState.Contains("发布"))
                            {
                                return Err("失败：图纸已发布，无法追加版次（" + (de.Name ?? name) + "）");
                            }
                            existingDuplicate = de;
                            _uploadCurrentObjectId = de.ObjectID;
                            try { diag["before_iterator_no"] = de.IteratorNo; } catch { }
                            try { diag["before_history_count"] = (de.GetHistoryDrawings() ?? new IEAPDrawingIteratorEntity[0]).Length; } catch { }
                            diag["append_iteration_requested"] = true;
                            continue;
                        }
                        dr = de;
                        _uploadCurrentObjectId = de.ObjectID;
                        break;
                    }
                }
            }
            catch { }
            if (dr != null)
            {
                if (onDuplicate == "new_version")
                {
                    var nv = AddNewVersion(dr, filePath, diag);
                    if (nv != null)
                    {
                        return Ok(new Dictionary<string, object>
                        {
                            { "object_id", dr.ObjectID },
                            { "name", dr.Name },
                            { "file_name", dr.FileName },
                            { "draw_no", dr.DrawNo },
                            { "iterator_no", diag.ContainsKey("iterator_no") ? diag["iterator_no"] : null },
                            { "class_id", dr.ClassID }, { "class_name", GetClassNameByID(sess, dr.ClassID) },
                            { "attached", true },
                            { "new_version", true },
                            { "method", "new_version" },
                            { "diag", diag }
                        });
                    }
                    if (diag.ContainsKey("new_version_copy_failed"))
                    {
                        var failed = Err("新增版次失败：未能覆盖检出文件，已放弃检入（" + (dr.Name ?? "") + "）");
                        failed["stage"] = "new_version";
                        failed["object_id"] = dr.ObjectID;
                        failed["partial"] = true;
                        failed["detail"] = "copy_failed";
                        failed["diag"] = diag;
                        return failed;
                    }
                    diag["new_version_fallback"] = "reuse";
                }
                return Ok(new Dictionary<string, object>
                {
                    { "object_id", dr.ObjectID },
                    { "name", dr.Name },
                    { "file_name", dr.FileName },
                    { "class_name", diag.ContainsKey("class_name") ? diag["class_name"] : null },
                    { "attached", true },
                    { "reused", true },
                    { "method", "reuse" },
                    { "diag", diag }
                });
            }
            try { diag["allow_2d"] = part.AllowNew2DDrawing(); } catch { diag["allow_2d"] = "err"; }
            try { diag["allow_capp"] = part.AllowNewCAPPDrawing(); } catch { diag["allow_capp"] = "err"; }
            try { diag["has_children"] = (part.Children(null) ?? new IEAPPart2PartEntity[0]).Length; } catch { }
            try { diag["drawing_count"] = (part.GetAllMaterDrawing() ?? new CompositeEntity[0]).Length; } catch { }
            if (drawingType != (int)DrawingType.DrawingCAPP)
            {
                try
                {
                    dr = part.AttachFile((DrawingType)drawingType, filePath, false, false);
                    diag["attach"] = dr != null ? "ok" : "null";
                    if (dr != null) { attachMethod = "attach"; _uploadCurrentObjectId = dr.ObjectID; }
                }
                catch (Exception ex)
                {
                    diag["attach_err"] = ex.Message;
                }
                if (dr == null)
                {
                    try
                    {
                        IEAPDrawingEntity nd = null;
                        IEAPPart2PartEntity link = null;
                        part.AttachFileAsChild((DrawingType)drawingType, filePath, false, false, out nd, out link);
                        dr = nd;
                        diag["attach_as_child"] = dr != null ? "ok" : "null";
                        if (dr != null) { attachMethod = "attach_as_child"; _uploadCurrentObjectId = dr.ObjectID; }
                    }
                    catch (Exception ex)
                    {
                        diag["attach_as_child_err"] = ex.Message;
                    }
                }
                if (dr == null && drawingType != (int)DrawingType.Document)
                {
                    try
                    {
                        dr = part.AttachFile(DrawingType.Document, filePath, false, true);
                        diag["attach_doc"] = dr != null ? "ok" : "null";
                        if (dr != null) { attachMethod = "attach_doc"; _uploadCurrentObjectId = dr.ObjectID; }
                    }
                    catch (Exception ex)
                    {
                        diag["attach_doc_err"] = ex.Message;
                    }
                }
            }
            CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObjectBase lowMaster = null;
            if (dr == null)
            {
                try
                {
                    var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
                    var session = engine != null ? engine.CurSession : null;
                    if (session == null)
                    {
                        diag["low_session"] = "unavailable";
                    }
                    else
                    {
                        var store = session.ObjectStore;
                        var partObj = store.RetrieveObject((short)1019, parentId)
                                      as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
                        if (partObj == null)
                        {
                            diag["low_part"] = "null";
                        }
                        else
                        {
                            var dm = session.DocumentManager;
                            string targetClass = GetClassNameByID(session,
                                drawingType == (int)DrawingType.DrawingCAPP ? (short)1003 : (short)1002);
                            var classList = new List<string>();
                            if (!string.IsNullOrEmpty(targetClass)) classList.Add(targetClass);
                            classList.Add("Document");
                            classList.Add("Drawing");
                            classList.Add("CappDocument");
                            string[] classes = classList.ToArray();
                            foreach (var cls in classes)
                            {
                                try
                                {
                                    var master = dm.InputFileToPart(
                                        partObj, cls, filePath, false, false,
                                        CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.EnumTransactionMode.RequireNew);
                                    if (master != null)
                                    {
                                        diag["low_ok_class"] = cls;
                                        attachMethod = "low:" + cls;
                                        lowMaster = master as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObjectBase;
                                        if (lowMaster != null) _uploadCurrentObjectId = lowMaster.ObjectId;
                                        break;
                                    }
                                }
                                catch (Exception ex)
                                {
                                    diag["low_" + cls + "_err"] = ex.Message;
                                }
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    diag["low_err"] = ex.Message;
                }
            }
            if (dr == null && lowMaster != null)
            {
                try
                {
                    if (!string.IsNullOrEmpty(name))
                    {
                        lowMaster.SetValue("name", name);
                        lowMaster.Update();
                    }
                    if (!string.IsNullOrEmpty(drawNo))
                    {
                        try { lowMaster.SetValue("DrawNo", drawNo); lowMaster.Update(); } catch { }
                    }
                }
                catch { }
                return Ok(new Dictionary<string, object>
                {
                    { "object_id", lowMaster.ObjectId },
                    { "attached", true },
                    { "low_class", lowMaster.ClassName },
                    { "class_id", ClassIdByName(lowMaster.ClassName) },
                    { "class_name", lowMaster.ClassName },
                    { "method", attachMethod },
                    { "diag", diag }
                });
            }
            if (dr == null)
            {
                if (existingDuplicate != null)
                {
                    try { existingDuplicate.Retrieve(); } catch { }
                    int beforeCnt = diag.ContainsKey("before_history_count") ? Convert.ToInt32(diag["before_history_count"]) : 0;
                    string beforeItr = diag.ContainsKey("before_iterator_no") ? Convert.ToString(diag["before_iterator_no"]) : "";
                    int afterCnt = 0;
                    string afterItr = "";
                    try { afterCnt = (existingDuplicate.GetHistoryDrawings() ?? new IEAPDrawingIteratorEntity[0]).Length; } catch { }
                    try { afterItr = existingDuplicate.IteratorNo ?? ""; } catch { }
                    diag["after_history_count"] = afterCnt;
                    diag["after_iterator_no"] = afterItr;
                    if (afterCnt > beforeCnt || (!string.IsNullOrEmpty(afterItr) && afterItr != beforeItr))
                    {
                        return Ok(new Dictionary<string, object>
                        {
                            { "object_id", existingDuplicate.ObjectID },
                            { "name", existingDuplicate.Name },
                            { "file_name", existingDuplicate.FileName },
                            { "draw_no", existingDuplicate.DrawNo },
                            { "iterator_no", afterItr },
                            { "class_id", existingDuplicate.ClassID },
                            { "class_name", GetClassNameByID(sess, existingDuplicate.ClassID) },
                            { "attached", true },
                            { "new_version", true },
                            { "method", "append_iteration" },
                            { "diag", diag }
                        });
                    }
					// CAXA can expose the new history row before advancing the
					// current iterator. Return the known object so Python can
					// perform a fresh-session readback instead of replaying.
					var unverified = Ok(new Dictionary<string, object>
					{
						{ "object_id", existingDuplicate.ObjectID },
						{ "name", existingDuplicate.Name },
						{ "file_name", existingDuplicate.FileName },
						{ "draw_no", existingDuplicate.DrawNo },
						{ "iterator_no", afterItr },
						{ "class_id", existingDuplicate.ClassID },
						{ "class_name", GetClassNameByID(sess, existingDuplicate.ClassID) },
						{ "attached", false },
						{ "new_version", false },
						{ "method", "append_iteration_unverified" },
						{ "diag", diag }
					});
					unverified["stage"] = "upload_verify";
					unverified["partial"] = true;
					unverified["uncertain"] = true;
					unverified["error"] =
						"追加新版次失败：所有 AttachFile 路径均返回空";
					return unverified;
                }
                return Ok(new Dictionary<string, object> { { "diag", diag }, { "attached", false } });
            }
            if (diag.Count > 0)
            {
                if (!string.IsNullOrEmpty(drawNo)) { try { dr.DrawNo = drawNo; } catch { } }
                if (!string.IsNullOrEmpty(name)) { try { dr.Name = name; } catch { } }
                try { dr.Update(); } catch { }
                try { dr.Retrieve(); } catch { }
                try { diag["after_iterator_no"] = dr.IteratorNo; } catch { }
                try
                {
                    diag["after_history_count"] =
                        (dr.GetHistoryDrawings() ?? new IEAPDrawingIteratorEntity[0]).Length;
                }
                catch { }
                return Ok(new Dictionary<string, object>
                {
                    { "object_id", dr.ObjectID },
                    { "draw_no", dr.DrawNo },
                    { "name", dr.Name },
                    { "file_name", dr.FileName },
                    { "state_ext", dr.StateExt },
                    { "attached", true },
                    { "class_id", dr.ClassID },
                    { "class_name", GetClassNameByID(sess, dr.ClassID) },
                    { "method", attachMethod },
                    { "iterator_no", diag.ContainsKey("after_iterator_no") ? diag["after_iterator_no"] : null },
                    { "diag", diag }
                });
            }
        }
        else return Err("unsupported parent_kind: " + parentKind);
        if (dr == null) return Err("AttachFile returned null");
        if (!string.IsNullOrEmpty(drawNo)) { try { dr.DrawNo = drawNo; } catch { } }
        if (!string.IsNullOrEmpty(name)) { try { dr.Name = name; } catch { } }
        try { dr.Update(); } catch { }
        return Ok(new Dictionary<string, object>
        {
            { "object_id", dr.ObjectID },
            { "draw_no", dr.DrawNo },
            { "name", dr.Name },
            { "file_name", dr.FileName },
            { "state_ext", dr.StateExt },
            { "attached", true },
            { "class_id", dr.ClassID }
        });
    }

    // 重复图号 → 在当前文档上新增版本/版次。
    // 账号无“反审”（NewRelease）权限，且只处理未发布文件：
    // 流程 = CheckOut（未发布可直接检出）→ 用新 PDF 覆盖检出文件 → CheckIn（生成新版次/版本）。
    // 若文档已发布/被他人检出，CheckOut 会失败，上层回退“复用已有文档”。
    static Dictionary<string, object> AddNewVersion(IEAPDrawingEntity dr, string newFile, Dictionary<string, object> diag)
    {
        var steps = new Dictionary<string, object>();
        diag["new_version_steps"] = steps;
        try
        {
            try { diag["state_ext"] = dr.StateExt; } catch { }
            try { diag["iterator_before"] = dr.IteratorNo; } catch { }
            try { diag["history_before"] = (dr.GetHistoryDrawings() ?? new IEAPDrawingIteratorEntity[0]).Length; } catch { }

            string checkoutPath = null;
            try { checkoutPath = dr.CheckOut(); } catch (Exception ex) { steps["checkout_err"] = ex.Message; }
            if (string.IsNullOrEmpty(checkoutPath))
            {
                // 检出没给路径：尝试显式下载到临时目录
                try
                {
                    string tmp = Path.Combine(Path.GetTempPath(), "caxa_uploader_" + Environment.TickCount);
                    Directory.CreateDirectory(tmp);
                    if (dr.DownLoadFile(tmp, false))
                    {
                        string[] files = Directory.GetFiles(tmp);
                        if (files.Length == 1)
                        {
                            checkoutPath = files[0];
                        }
                        else if (files.Length > 0)
                        {
                            string baseName = Path.GetFileName(newFile);
                            checkoutPath = Array.Find(files, f =>
                                string.Equals(Path.GetFileName(f), baseName, StringComparison.OrdinalIgnoreCase)) ?? files[0];
                        }
                    }
                }
                catch (Exception ex)
                {
                    steps["download_err"] = ex.Message;
                }
            }
            steps["checkout_path"] = checkoutPath ?? "";

            if (!string.IsNullOrEmpty(checkoutPath))
            {
                try
                {
                    File.Copy(newFile, checkoutPath, true);
                    steps["copy"] = true;
                }
                catch (Exception ex)
                {
                    steps["copy_err"] = ex.Message;
                    diag["new_version_copy_failed"] = true;
                }
            }
            else
            {
                diag["new_version_copy_failed"] = true;
            }

            bool ci = false;
            if (string.IsNullOrEmpty(checkoutPath) || diag.ContainsKey("new_version_copy_failed"))
            {
                // 没有可覆盖的检出文件或覆盖失败：绝不检入，避免把旧内容当新版次。
                steps["skip_checkin"] = "copy_failed";
                try { dr.UnDoCheckOut(); steps["undo_checkout"] = true; } catch { }
            }
            else
            {
                try { ci = dr.CheckIn(); } catch (Exception ex) { steps["checkin_err"] = ex.Message; }
                steps["checkin"] = ci;
                if (!ci)
                {
                    try { dr.UnDoCheckOut(); steps["undo_checkout"] = true; } catch { }
                }
            }
            try { diag["iterator_no"] = dr.IteratorNo; } catch { }
            try { diag["history_count"] = (dr.GetHistoryDrawings() ?? new IEAPDrawingIteratorEntity[0]).Length; } catch { }
            return ci ? steps : null;
        }
        catch (Exception ex)
        {
            steps["fatal"] = ex.Message;
            return null;
        }
    }

    // 列出某零件“图纸”页里的所有文档
    static Dictionary<string, object> CmdPartDrawings(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int pid = Convert.ToInt32(a["part_id"]);
        bool refresh = a.ContainsKey("refresh") && Convert.ToBoolean(a["refresh"]);
        IEAPPartEntity part = _svc.GetPart(pid);
        if (part == null) return Err("part not found: " + pid);
        if (refresh)
        {
            try { part.Refresh(); }
            catch { try { part.Retrieve(); } catch { } }
        }
        var list = new List<object>();
        try
        {
            foreach (var comp in part.GetAllMaterDrawing() ?? new CompositeEntity[0])
            {
                IEAPDrawingEntity de = comp.Entity as IEAPDrawingEntity;
                if (de == null) continue;
                if (refresh)
                {
                    try { de.Refresh(); }
                    catch { try { de.Retrieve(); } catch { } }
                }
                var d = new Dictionary<string, object>();
                try { d["object_id"] = de.ObjectID; } catch { }
                try { d["class_id"] = de.ClassID; } catch { }
                try
                {
                    var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
                    var session = engine != null ? engine.CurSession : null;
                    d["class_name"] = GetClassNameByID(session, de.ClassID);
                }
                catch { }
                try { d["name"] = de.Name; } catch { }
                try { d["draw_no"] = de.DrawNo; } catch { }
                try { d["state"] = de.StateExt; } catch { }
                try { d["file_name"] = de.FileName; } catch { }
                try { d["iterator_no"] = de.IteratorNo; } catch { }
                try { d["history_count"] = (de.GetHistoryDrawings() ?? new IEAPDrawingIteratorEntity[0]).Length; } catch { }
                list.Add(d);
            }
        }
        catch (Exception ex)
        {
            return Err("list drawings error: " + ex.Message);
        }
        return Ok(new Dictionary<string, object> { { "count", list.Count }, { "drawings", list } });
    }

    // 读取某张图纸的版次历史（只读诊断）。
    static Dictionary<string, object> CmdDrawingHistory(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int pid = Convert.ToInt32(a["part_id"]);
        int drawingId = Convert.ToInt32(a["drawing_id"]);
        IEAPPartEntity part = _svc.GetPart(pid);
        if (part == null) return Err("part not found: " + pid);
        IEAPDrawingEntity drawing = null;
        foreach (var comp in part.GetAllMaterDrawing() ?? new CompositeEntity[0])
        {
            IEAPDrawingEntity candidate = comp.Entity as IEAPDrawingEntity;
            if (candidate != null && candidate.ObjectID == drawingId)
            {
                drawing = candidate;
                break;
            }
        }
        if (drawing == null) return Err("drawing not found under part: " + drawingId);
        try { drawing.Refresh(); }
        catch { try { drawing.Retrieve(); } catch { } }
        var history = new List<object>();
        foreach (var item in drawing.GetHistoryDrawings() ?? new IEAPDrawingIteratorEntity[0])
        {
            var row = new Dictionary<string, object>
            {
                { "type", item.GetType().FullName }
            };
            foreach (var prop in item.GetType().GetProperties())
            {
                try
                {
                    object value = prop.GetValue(item, null);
                    row[prop.Name] = value == null ? null : value.ToString();
                }
                catch { }
            }
            history.Add(row);
        }
        var result = new Dictionary<string, object>
        {
            { "drawing_id", drawing.ObjectID },
            { "iterator_no", drawing.IteratorNo },
            { "history", history }
        };
        return Ok(result);
    }

    // 改零件：图号 / 名称（高层属性 + 低层 SetValue 兜底）
    static Dictionary<string, object> CmdSetPart(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int id = Convert.ToInt32(a["object_id"]);
        string identity = a.ContainsKey("identity") ? Convert.ToString(a["identity"]) : null;
        string name = a.ContainsKey("name") ? Convert.ToString(a["name"]) : null;
        IEAPPartEntity pt = _svc.GetPart(id);
        if (pt == null) return Err("part not found: " + id);
        var res = new Dictionary<string, object>();
        if (!string.IsNullOrEmpty(name))
        {
            try { pt.Name = name; res["name"] = name; }
            catch (Exception ex) { res["name_err"] = ex.Message; }
        }
        if (!string.IsNullOrEmpty(identity))
        {
            try
            {
                var prop = pt.GetType().GetProperty("IdentityNo");
                if (prop != null && prop.CanWrite)
                {
                    prop.SetValue(pt, identity, null);
                    res["identity_high"] = identity;
                }
                else
                {
                    res["identity_high"] = "not writable";
                }
            }
            catch (Exception ex) { res["identity_high_err"] = ex.Message; }
        }
        try { pt.Update(); res["updated"] = true; }
        catch (Exception ex) { res["update_err"] = ex.Message; }
        if (!string.IsNullOrEmpty(identity))
        {
            try
            {
                var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
                var session = engine != null ? engine.CurSession : null;
                var store = session != null ? session.ObjectStore : null;
                var obj = store != null
                    ? store.RetrieveObject((short)1019, id)
                      as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObjectBase
                    : null;
                if (obj != null)
                {
                    obj.SetValue("IdentityNo", identity);
                    obj.Update();
                    res["identity_low"] = identity;
                }
            }
            catch (Exception ex) { res["identity_low_err"] = ex.Message; }
        }
        var after = _svc.GetPart(id);
        if (after != null)
        {
            res["identity_now"] = after.IdentityNo;
            res["name_now"] = after.Name;
        }
        return Ok(res);
    }

    // 删除图纸（文档）：_svc.GetDrawing(class, id).Delete()
    static Dictionary<string, object> CmdDeleteDrawing(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int id = Convert.ToInt32(a["object_id"]);
        short cls = a.ContainsKey("class_id") ? Convert.ToInt16(a["class_id"]) : (short)1002;
        try
        {
            IEAPDrawingEntity dr = _svc.GetDrawing(cls, id);
            if (dr == null) return Err("drawing not found: " + id);
            var res = new Dictionary<string, object>();
            try { res["name"] = dr.Name; } catch { }
            try { res["iterator_no"] = dr.IteratorNo; } catch { }
            try { res["exists_before"] = dr.Exists(); } catch { }
            _deleteDialogAutoConfirm = true;
            try { dr.Delete(); }
            finally { _deleteDialogAutoConfirm = false; }
            try
            {
                var after = _svc.GetDrawing(cls, id);
                res["get_after"] = after != null;
                if (after != null)
                {
                    try { res["exists_after"] = after.Exists(); } catch { }
                }
                res["deleted"] = after == null;
            }
            catch (Exception ex2)
            {
                res["after_err"] = ex2.Message;
                res["deleted"] = true;
            }
            return Ok(res);
        }
        catch (Exception ex)
        {
            return Err("delete failed: " + ex.Message);
        }
    }

    // 改图纸（文档）的名称 / 图号：低层对象属性 + Update
    static Dictionary<string, object> CmdSetDrawing(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int id = Convert.ToInt32(a["object_id"]);
        string name = a.ContainsKey("name") ? Convert.ToString(a["name"]) : null;
        string drawNo = a.ContainsKey("draw_no") ? Convert.ToString(a["draw_no"]) : null;
        bool updated = false;
        try
        {
            var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
            var session = engine != null ? engine.CurSession : null;
            if (session == null) return Err("session unavailable");
            var store = session.ObjectStore;
            var obj = store.RetrieveObject((short)1002, id)
                      as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObjectBase;
            if (obj == null) return Err("drawing object not found: " + id);
            var res = new Dictionary<string, object>();
            if (!string.IsNullOrEmpty(name))
            {
                obj.SetValue("name", name);
                res["name"] = name;
            }
            if (!string.IsNullOrEmpty(drawNo))
            {
                try { obj.SetValue("DrawNo", drawNo); res["draw_no"] = drawNo; }
                catch (Exception ex) { res["draw_no_err"] = ex.Message; }
            }
            obj.Update();
            updated = true;
            res["class"] = obj.ClassName;
            res["updated"] = true;
            var after = _svc.GetDrawing((short)1002, id);
            if (after != null)
            {
                try { after.Retrieve(); } catch { }
                try { res["name_after"] = after.Name ?? ""; } catch { }
                try { res["draw_no_after"] = after.DrawNo ?? ""; } catch { }
            }
            if (!string.IsNullOrEmpty(name) &&
                !string.Equals(
                    Convert.ToString(res.ContainsKey("name_after") ? res["name_after"] : "").Trim(),
                    name.Trim(),
                    StringComparison.OrdinalIgnoreCase))
            {
                res["ok"] = false;
                res["error"] = "set drawing name readback mismatch";
                return res;
            }
            return Ok(res);
        }
        catch (Exception ex)
        {
            if (updated)
            {
                return WriteError(
                    "set drawing failed after update: " + ex.Message,
                    "set_drawing:after_update",
                    new Dictionary<string, object> { { "drawing", id } },
                    id,
                    ex.ToString());
            }
            return Err("set drawing error: " + ex.Message);
        }
    }

    static Dictionary<string, object> CmdNewRelease(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int id = Convert.ToInt32(a["drawing_id"]);
        IEAPDrawingEntity dr = FindDrawingById(id);
        if (dr == null) return Err("drawing not found");
        bool ok = dr.NewRelease();
        return ok ? Ok() : Err("NewRelease returned false");
    }

    static Dictionary<string, object> CmdRelease(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int id = Convert.ToInt32(a["drawing_id"]);
        short cls = a.ContainsKey("class_id") ? Convert.ToInt16(a["class_id"]) : (short)1002;
        IEAPDrawingEntity dr = _svc.GetDrawing(cls, id);
        if (dr == null) dr = FindDrawingById(id);
        if (dr == null) return Err("drawing not found: " + id);
        var result = new Dictionary<string, object>();
        try { result["object_id"] = dr.ObjectID; } catch { }
        try { result["name"] = dr.Name; } catch { }
        try { result["iterator_no"] = dr.IteratorNo; } catch { }
        try { result["state_before"] = dr.StateExt; } catch { }
        bool ok = dr.Release();
        result["released"] = ok;
        try { dr.Retrieve(); } catch { }
        try { result["iterator_no_after"] = dr.IteratorNo; } catch { }
        try { result["state_after"] = dr.StateExt; } catch { }
        return ok ? Ok(result) : Err("Release returned false");
    }

    // Read-only workflow recovery evidence.  The UI supplies only the visible
    // process instance id; this command reads state and linked document ids.
    static Dictionary<string, object> CmdWorkflowInfo(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int processId = Convert.ToInt32(a["process_instance_id"]);
        if (processId <= 0) return Err("process_instance_id must be positive");
        var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
        var session = engine != null ? engine.CurSession : null;
        if (session == null) return Err("session unavailable");
        try
        {
            var processObject = session.ObjectStore.RetrieveObject((short)2466, processId)
                as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
            if (processObject == null) return Err("workflow not found: " + processId);
            var result = new Dictionary<string, object>
            {
                { "process_instance_id", processId }
            };
            try { result["name"] = processObject.GetValueAsString("name") ?? ""; }
            catch { result["name"] = ""; }
            try { result["process_state"] = processObject.GetValueAsString("state") ?? ""; }
            catch { result["process_state"] = ""; }
            try { result["start_time"] = processObject.GetValueAsString("start_time") ?? ""; }
            catch { result["start_time"] = ""; }
            var linkedDocumentIds = new List<int>();
            var related = processObject.RetrieveDirectChildren("WFInstanceWithDocument")
                as System.Collections.IEnumerable;
            if (related != null)
            {
                foreach (object raw in related)
                {
                    var document = raw as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObjectBase;
                    if (document != null) linkedDocumentIds.Add(document.ObjectId);
                }
            }
            result["linked_document_ids"] = linkedDocumentIds;
            result["document_count"] = linkedDocumentIds.Count;
            return Ok(result);
        }
        catch (Exception ex)
        {
            return Err("workflow_info error: " + ex.Message);
        }
    }

    static Dictionary<string, object> CmdStartWorkflow(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        if (!a.ContainsKey("drawing_ids") || a["drawing_ids"] == null)
            return Err("drawing_ids is required");

        var drawingIds = new List<int>();
        var drawingClassIds = new List<int>();
        var values = a["drawing_ids"] as System.Collections.IEnumerable;
        if (values != null)
        {
            foreach (object value in values)
                drawingIds.Add(Convert.ToInt32(value));
        }
        if (a.ContainsKey("document_class_ids") &&
            a["document_class_ids"] is System.Collections.IEnumerable)
        {
            foreach (object value in (System.Collections.IEnumerable)a["document_class_ids"])
                drawingClassIds.Add(Convert.ToInt32(value));
        }
        if (drawingIds.Count == 0) return Err("drawing_ids is empty");
        var uniqueDrawingIds = new List<int>();
        var seenDrawingIds = new HashSet<int>();
        var classIdByDocumentId = new Dictionary<int, int>();
        for (int index = 0; index < drawingIds.Count; index++)
        {
            int drawingId = drawingIds[index];
            int classId = index < drawingClassIds.Count
                ? drawingClassIds[index]
                : 1002;
            if (seenDrawingIds.Add(drawingId))
            {
                uniqueDrawingIds.Add(drawingId);
                classIdByDocumentId[drawingId] = classId;
            }
        }

        uint templateId = a.ContainsKey("template_id")
            ? Convert.ToUInt32(a["template_id"]) : 1u;
        uint templateInstanceId = a.ContainsKey("template_instance_id")
            ? Convert.ToUInt32(a["template_instance_id"]) : 31u;
        string processDefId = a.ContainsKey("process_def_id")
            ? Convert.ToString(a["process_def_id"]) : "2";
        string activityDefId = a.ContainsKey("activity_def_id")
            ? Convert.ToString(a["activity_def_id"]) : "4";
        uint performerId = a.ContainsKey("performer_id")
            ? Convert.ToUInt32(a["performer_id"]) : 66u;
        string processName = a.ContainsKey("process_name")
            ? Convert.ToString(a["process_name"]) : "";
        string processDescription = a.ContainsKey("process_description")
            ? Convert.ToString(a["process_description"]) : "";
        string processNotify = a.ContainsKey("process_notify")
            ? Convert.ToString(a["process_notify"]) : "";
        bool execute = a.ContainsKey("execute") && Convert.ToBoolean(a["execute"]);

        var drawings = new List<Dictionary<string, object>>();
        foreach (int drawingId in uniqueDrawingIds)
        {
            int classId = classIdByDocumentId[drawingId];
            IEAPDrawingEntity drawing = _svc.GetDrawing((short)classId, drawingId);
            if (drawing == null) drawing = FindDrawingById(drawingId);
            if (drawing == null)
                return Err("document not found: class=" + classId + ", id=" + drawingId);
            var item = new Dictionary<string, object>
            {
                { "object_id", drawing.ObjectID },
                { "class_id", classId }
            };
            try { item["name"] = drawing.Name; } catch { }
            try { item["state"] = drawing.StateExt; } catch { }
            try { item["iterator_no"] = drawing.IteratorNo; } catch { }
            drawings.Add(item);
        }

        var result = new Dictionary<string, object>
        {
            { "dry_run", !execute },
            { "template_object_id", templateId },
            { "template_instance_id", templateInstanceId },
            { "process_def_id", processDefId },
            { "activity_def_id", activityDefId },
            { "performer_id", performerId },
            { "document_link_class_id", 3305 },
            { "document_ids", uniqueDrawingIds },
            { "document_class_ids",
                uniqueDrawingIds.Select(id => classIdByDocumentId[id]).ToList() },
            { "drawings", drawings }
        };
        if (!execute) return Ok(result);

        if (!a.ContainsKey("server_ip") || !a.ContainsKey("server_port") || !a.ContainsKey("user_id"))
            return Err("server_ip, server_port, user_id are required when execute=true");

        Interop.EAPLib.CxDalWfConnectionClass connection = null;
        Interop.EAPLib.CxDalWfProcessClass process = null;
        uint connectionId = 0;
        uint processId = 0;
        string stage = "connect";
        // 流程 create 成功后的任何失败都必须带上已建流程 ID 与阶段，
        // 供上层保存为“待核实”，不能按普通失败重放。
        Func<string, Dictionary<string, object>> fail = msg =>
        {
            var resp = Err(msg);
            resp["stage"] = stage;
            resp["partial"] = processId != 0;
            if (processId != 0) resp["created_process_instance_id"] = processId;
            resp["process_instance_id"] = processId;
            resp["result"] = result;
            return resp;
        };
        try
        {
            var serverInfo = new Interop.EAPLib.CxServerInfoClass
            {
                server_ip = Convert.ToString(a["server_ip"]),
                server_port = Convert.ToUInt32(a["server_port"]),
                server_name = "Workflow"
            };
            connection = new Interop.EAPLib.CxDalWfConnectionClass();
            connection.server_type = (int)Interop.EAPLib.PLMSERVERTYPE.PLM_SERVERTYPE_WORKFLOWSERVER;
            connection.server_info = serverInfo;
            stage = "connect";
            connectionId = connection.connect(Convert.ToUInt32(a["user_id"]));
            connection.connection_id = connectionId;
            if (connectionId == 0) return fail("workflow connect returned 0");

            stage = "create";
            process = new Interop.EAPLib.CxDalWfProcessClass();
            process.server_type = (int)Interop.EAPLib.PLMSERVERTYPE.PLM_SERVERTYPE_WORKFLOWSERVER;
            process.server_info = serverInfo;
            process.connection_id = connectionId;
            processId = process.create(
                templateId,
                templateInstanceId,
                processDefId,
                processName,
                processDescription,
                processNotify);
            result["workflow_connection_id"] = connectionId;
            result["process_instance_id"] = processId;
            result["stage"] = stage;
            if (processId == 0) return fail("workflow create returned 0");

            stage = "retrieve_process";
            var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
            var session = engine != null ? engine.CurSession : null;
            if (session == null) return fail("session unavailable");
            var store = session.ObjectStore;
            var processObject = store.RetrieveObject((short)2466, (int)processId)
                as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
            if (processObject == null) return fail("created workflow object not retrievable: " + processId);

            stage = "link_documents";
            var linkIds = new List<int>();
            foreach (int drawingId in uniqueDrawingIds)
            {
                int classId = classIdByDocumentId[drawingId];
                var drawingObject = store.RetrieveObject((short)classId, drawingId)
                    as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
                if (drawingObject == null)
                {
                    result["failed_drawing_id"] = drawingId;
                    result["failed_document_class_id"] = classId;
                    result["document_link_ids"] = linkIds;
                    return fail(
                        "document object not retrievable: class=" + classId +
                        ", id=" + drawingId);
                }
                var link = store.NewOneLevelLink(3305, processObject, drawingObject, null);
                link.Insert();
                try { linkIds.Add(link.ObjectId); } catch { }
            }
            result["document_link_ids"] = linkIds;

            stage = "designate_performer";
            process.designate_dynamic_performer(
                processId, processDefId, activityDefId, 1,
                performerId.ToString());
            stage = "start";
            process.start(processId);

            // A successful start call is not enough evidence: the process may
            // still be OPEN/NOT_STARTED, and document links may have been lost.
            // Read back the live WFProcessInst state and linked documents.
            stage = "verify_process";
            string processState = "";
            string processStartTime = "";
            var linkedDocumentIds = new List<int>();
            var expectedDocuments = new HashSet<int>(uniqueDrawingIds);
            string verifyError = "";
            bool verified = false;
            for (int attempt = 0; attempt < 10; attempt++)
            {
                if (attempt > 0) System.Threading.Thread.Sleep(200);
                linkedDocumentIds.Clear();
                try
                {
                    var verifiedObject = store.RetrieveObject((short)2466, (int)processId)
                        as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
                    if (verifiedObject == null)
                    {
                        verifyError = "created workflow object disappeared";
                        continue;
                    }
                    try { processState = verifiedObject.GetValueAsString("state") ?? ""; }
                    catch (Exception ex) { verifyError = "state read failed: " + ex.Message; }
                    try { processStartTime = verifiedObject.GetValueAsString("start_time") ?? ""; }
                    catch { processStartTime = ""; }
                    var related = verifiedObject.RetrieveDirectChildren("WFInstanceWithDocument")
                        as System.Collections.IEnumerable;
                    if (related != null)
                    {
                        foreach (object raw in related)
                        {
                            var doc = raw as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObjectBase;
                            if (doc != null) linkedDocumentIds.Add(doc.ObjectId);
                        }
                    }
                    var actualDocuments = new HashSet<int>(linkedDocumentIds);
                    if (processState == "2" &&
                        actualDocuments.SetEquals(expectedDocuments))
                    {
                        verified = true;
                        verifyError = "";
                        break;
                    }
                    verifyError = "state=" + processState
                        + ", linked=" + linkedDocumentIds.Count
                        + "/" + uniqueDrawingIds.Count;
                }
                catch (Exception ex)
                {
                    verifyError = ex.Message;
                }
            }
            result["process_state"] = processState;
            result["process_start_time"] = processStartTime;
            result["linked_document_ids"] = linkedDocumentIds;
            result["state_verified"] = verified;
            result["documents_verified"] = verified;
            if (!verified)
            {
                return fail("流程启动后回读未通过（" + verifyError + "）");
            }
            result["started"] = true;
            result["stage"] = "started";
            return Ok(result);
        }
        catch (Exception ex)
        {
            var errResp = Err("start_workflow 失败: " + ex.Message);
            errResp["detail"] = ex.ToString();
            errResp["stage"] = stage;
            errResp["partial"] = processId != 0;
            if (processId != 0) errResp["created_process_instance_id"] = processId;
            errResp["process_instance_id"] = processId;
            errResp["result"] = result;
            return errResp;
        }
        finally
        {
            if (connection != null && connectionId != 0)
            {
                try { connection.disconnect(connectionId); } catch { }
            }
        }
    }

    static Dictionary<string, object> CmdLinkPart(Dictionary<string, object> a)
    {
        int parentId = 0;
        int childId = 0;
        try
        {
            parentId = Convert.ToInt32(a["parent_id"]);
            childId = Convert.ToInt32(a["child_id"]);
            return CmdLinkPartCore(a);
        }
        catch (Exception ex)
        {
            return WriteError(
                "link_part failed: " + ex.Message,
                "link_part:after_write",
                new Dictionary<string, object>
                {
                    { "parent", parentId },
                    { "child", childId }
                },
                0,
                ex.ToString());
        }
    }

    static IEAPPart2PartEntity FindPart2PartChild(
        IEAPPartEntity parent,
        int childId)
    {
        parent.Refresh();
        int parentId = parent.ObjectID;
        bool incomplete = false;
        foreach (var link in parent.Children(null) ?? new IEAPPart2PartEntity[0])
        {
            int? linkedChildId = ReadPart2PartChildId(link, parentId);
            if (!linkedChildId.HasValue)
            {
                incomplete = true;
                continue;
            }
            if (linkedChildId.Value == childId) return link;
        }
        if (incomplete) throw new InvalidOperationException("unreadable Part2Part endpoint");
        return null;
    }

    static int? ReadPart2PartChildId(IEAPPart2PartEntity link, int parentId)
    {
        if (link == null) return null;
        try
        {
            IEAPPartEntity child = link.RefSubPart;
            if (child != null && child.ObjectID > 0) return child.ObjectID;
        }
        catch { }

        // Some CAXA 2011 clients cannot expose RefSubPart on an existing
        // relation. Read the exact relation's endpoints via the object store.
        try
        {
            var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
            var session = engine != null ? engine.CurSession : null;
            if (session == null) return null;
            var rawLink = session.ObjectStore.RetrieveObject(link.ClassID, link.ObjectID)
                as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxLinkObject;
            if (rawLink == null) return null;
            var roleA = rawLink.RoleAObject;
            var roleB = rawLink.RoleBObject;
            if (roleA != null && roleA.ObjectId == parentId &&
                roleB != null && roleB.ObjectId > 0)
            {
                return roleB.ObjectId;
            }
        }
        catch { }
        return null;
    }

    static Dictionary<string, object> CmdLinkPartCore(Dictionary<string, object> a)
    {
        if (_svc == null || !_svc.HasLogin) return Err("not logged in");
        int parentId = Convert.ToInt32(a["parent_id"]);
        int childId = Convert.ToInt32(a["child_id"]);
        bool reference = a.ContainsKey("reference") && Convert.ToBoolean(a["reference"]);
        bool productLink = a.ContainsKey("product_link") && Convert.ToBoolean(a["product_link"]);
        int quantity = a.ContainsKey("quantity") ? Convert.ToInt32(a["quantity"]) : 1;
        if (productLink)
        {
            IEAPProductEntity prod = _svc.GetProduct(parentId);
            IEAPPartEntity part = _svc.GetPart(childId);
            if (prod == null || part == null) return Err("product/part not found");
            IEAPPrd2PartEntity cond = _svc.NewPrd2Part();
            try { cond.Reference = reference; } catch { }
            IEAPPrd2PartEntity link = prod.LinkRelatedPart(part, cond);
            if (link != null)
            {
                return Ok(new Dictionary<string, object> { { "link", "product_part" }, { "link_id", link.ObjectID } });
            }
            // 高层失败：改用低层 NewOneLevelLink 建产品-零件链接
            try
            {
                var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
                var session = engine != null ? engine.CurSession : null;
                if (session == null) return Err("LinkRelatedPart returned null（会话不可用）");
                short linkClassId = 0;
                try
                {
                    var lc = session.MetaInfo.GetLinkClass(1012, 1019);
                    var lb = lc as CAXA.A5.EAP.CommonLibrary.MetaInfo.ICaxClassBase;
                    if (lb != null) linkClassId = lb.ClassId;
                }
                catch { }
                if (linkClassId == 0)
                {
                    foreach (var p in GetAllProductsList())
                    {
                        try
                        {
                            var links = p.RelatedRootParts((IEAPPrd2PartEntity)null);
                            if (links != null && links.Length > 0)
                            {
                                linkClassId = links[0].ClassID;
                                break;
                            }
                        }
                        catch { }
                    }
                }
                if (linkClassId == 0) return Err("LinkRelatedPart returned null（无法确定产品-零件链接类）");
                var store = session.ObjectStore;
                var prodObj = store.RetrieveObject(1012, parentId)
                               as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
                var partObj = store.RetrieveObject(1019, childId)
                              as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
                if (prodObj == null || partObj == null) return Err("LinkRelatedPart returned null（对象不可用）");
                var newLink = store.NewOneLevelLink(linkClassId, prodObj, partObj, null);
                if (!newLink.Insert())
                {
                    return WriteError(
                        "product-part link insert returned false",
                        "link_part:product_low",
                        new Dictionary<string, object>
                        {
                            { "product", parentId },
                            { "part", childId }
                        },
                        childId);
                }
                return Ok(new Dictionary<string, object> { { "link", "product_part_low" }, { "link_class", linkClassId } });
            }
            catch (Exception ex)
            {
                return WriteError(
                    "LinkRelatedPart returned null（低层也失败：" +
                    ex.Message + "）",
                    "link_part:product_low",
                    new Dictionary<string, object>
                    {
                        { "product", parentId },
                        { "part", childId }
                    },
                    childId,
                    ex.ToString());
            }
        }
        IEAPPartEntity parent = _svc.GetPart(parentId);
        IEAPPartEntity child = _svc.GetPart(childId);
        if (parent == null || child == null) return Err("part not found");
        IEAPPart2PartEntity existingPartLink =
            FindPart2PartChild(parent, childId);
        if (existingPartLink != null)
        {
            return Ok(new Dictionary<string, object>
            {
                { "link", "part_part_existing" },
                { "link_id", existingPartLink.ObjectID },
                { "reference", existingPartLink.Reference },
                { "existing", true }
            });
        }
        // A borrow relation may legitimately exist for the same child under
        // several parents.  Use the object-store link insert directly so the
        // high-level LinkSubPart implementation cannot reuse or move the
        // relation that was previously attached to another parent.
        if (!reference)
        {
            IEAPPart2PartEntity cond2 = null;
            try { cond2 = _svc.NewPart2Part(); } catch { }
            if (cond2 != null)
            {
                try { cond2.Reference = false; } catch { }
                try { cond2.AssemblyQuantity = quantity; } catch { }
                string highError = null;
                try
                {
                    IEAPPart2PartEntity link2 = parent.LinkSubPart(child, cond2);
                    if (link2 != null)
                    {
                        return Ok(new Dictionary<string, object>
                        {
                            { "link", "part_part" },
                            { "link_id", link2.ObjectID },
                            { "reference", link2.Reference }
                        });
                    }
                }
                catch (Exception ex)
                {
                    highError = ex.Message;
                }

                // A null return or an exception can follow a completed write.
                // FindPart2PartChild throws if absence cannot be established.
                IEAPPart2PartEntity recoveredLink = FindPart2PartChild(parent, childId);
                if (recoveredLink != null)
                {
                    return Ok(new Dictionary<string, object>
                    {
                        { "link", highError == null ? "part_part_after_null" : "part_part_after_exception" },
                        { "link_id", recoveredLink.ObjectID },
                        { "reference", recoveredLink.Reference },
                        { "recovered_after_exception", highError != null },
                        { "high_error", highError ?? "" }
                    });
                }
            }
        }
        // 低层建链：归属失败时后备，借用则始终直接插入独立关系。
        try
        {
            var engine = CAXA.A5.EAP.CommonLibrary.EAPLibEngine.Instance;
            var session = engine != null ? engine.CurSession : null;
            if (session == null) return Err("part link: 会话不可用");
            var store = session.ObjectStore;
            var parentObj = store.RetrieveObject((short)1019, parentId)
                            as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
            var childObj = store.RetrieveObject((short)1019, childId)
                           as CAXA.A5.EAP.CommonLibrary.Session.ObjectStore.ICaxObject;
            if (parentObj == null || childObj == null) return Err("part link: 对象不可用");
            var newLink = store.NewOneLevelLink(1037, parentObj, childObj, null);
            try { newLink.SetValue("Reference", reference); }
            catch (Exception ex)
            {
                // Borrowing must never fall through to the default ownership value.
                if (reference) return Err("cannot set borrow Reference=True: " + ex.Message);
            }
            try { newLink.SetValue("AssemblyQuantity", quantity); } catch { }
            bool inserted = newLink.Insert();
            if (!inserted) return Err("part link insert returned false");
            return Ok(new Dictionary<string, object>
            {
                { "link", "part_part_low" },
                { "link_id", newLink.ObjectId },
                { "reference", reference },
                { "quantity", quantity }
            });
        }
        catch (Exception ex)
        {
            return WriteError(
                "part link 低层失败：" + ex.Message,
                "link_part:part_low",
                new Dictionary<string, object>
                {
                    { "parent", parentId },
                    { "child", childId }
                },
                0,
                ex.ToString());
        }
    }

    // CAXA's document import path is an interactive STA COM path.  A plain
    // console entry point defaults to MTA and can make AttachFile return null
    // before the import dialog is created.
    [System.STAThread]
    static int Main(string[] args)
    {
        try
        {
            StartDialogAutoConfirmer();
            Console.OutputEncoding = new UTF8Encoding(false);
            Console.InputEncoding = new UTF8Encoding(false);
            string line;
            while ((line = Console.ReadLine()) != null)
            {
                if (string.IsNullOrWhiteSpace(line)) continue;
                Dictionary<string, object> resp;
                try
                {
                    var req = _json.Deserialize<Dictionary<string, object>>(line);
                    string cmd = Convert.ToString(req["cmd"]);
                    switch (cmd)
                    {
                        case "login": resp = CmdLogin(req); break;
                        case "ensure_plm_com_registered":
                            resp = CmdEnsurePlmComRegistered(); break;
                        case "logout": resp = CmdLogout(); break;
                        case "dump_tree": resp = CmdDumpTree(req); break;
                        case "find": resp = CmdFind(req); break;
                        case "list_products": resp = CmdListProducts(); break;
                        case "dump_product": resp = CmdDumpProduct(req); break;
                        case "dump_product_by_id":
                            resp = CmdDumpProductById(req); break;
                        case "find_product": resp = CmdFindProduct(req); break;
                        case "find_product_folder": resp = CmdFindProductFolder(req); break;
                        case "ensure_product_folder":
                            resp = CmdEnsureProductFolder(req); break;
                        case "link_product_to_folder":
                            resp = CmdLinkProductToFolder(req); break;
                        case "unlink_product_from_folder":
                            resp = CmdUnlinkProductFromFolder(req); break;
                        case "unlink_product_part":
                            resp = CmdUnlinkProductPart(req); break;
                        case "unlink_part_relation":
                            resp = CmdUnlinkPartRelation(req); break;
                        case "product_root_part": resp = CmdProductRootPart(req); break;
                        case "find_part": resp = CmdFindPart(req); break;
                        case "find_part_in_product": resp = CmdFindPartInProduct(req); break;
                        case "query_parts": resp = CmdQueryParts(req); break;
                        case "query_objects": resp = CmdQueryObjects(req); break;
                        case "query_links": resp = CmdQueryLinks(req); break;
                        case "wf_dal_probe": resp = CmdWorkflowDalProbe(req); break;
                        case "wf_connection_probe": resp = CmdWorkflowConnectionProbe(req); break;
                        case "related_objects": resp = CmdRelatedObjects(req); break;
                        case "class_info": resp = CmdClassInfo(req); break;
                        case "class_attributes":
                            resp = CmdClassAttributes(req); break;
                        case "part_info": resp = CmdPartInfo(req); break;
                        case "create_part": resp = CmdCreatePart(req); break;
                        case "ensure_part_material_code":
                            resp = CmdEnsurePartMaterialCode(req); break;
                        case "create_product": resp = CmdCreateProduct(req); break;
                        case "reference": resp = CmdReference(req); break;
                        case "upload": resp = CmdUpload(req); break;
                        case "new_release": resp = CmdNewRelease(req); break;
                        case "release_drawing": resp = CmdRelease(req); break;
                        case "workflow_info": resp = CmdWorkflowInfo(req); break;
                        case "start_workflow": resp = CmdStartWorkflow(req); break;
                        case "link_part": resp = CmdLinkPart(req); break;
                        case "part_drawings": resp = CmdPartDrawings(req); break;
                        case "drawing_history": resp = CmdDrawingHistory(req); break;
                        case "set_drawing": resp = CmdSetDrawing(req); break;
                        case "set_part": resp = CmdSetPart(req); break;
                        case "delete_drawing": resp = CmdDeleteDrawing(req); break;
                        case "dump_windows": resp = CmdDumpWindows(req); break;
                        case "set_auto_confirm": resp = CmdSetAutoConfirm(req); break;
                        default: resp = Err("unknown cmd: " + cmd); break;
                    }
                }
                catch (Exception ex)
                {
                    resp = Err(ex.Message);
                }
                Console.WriteLine(_json.Serialize(resp));
                Console.Out.Flush();
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine(_json.Serialize(Err(ex.ToString())));
            return 1;
        }
        return 0;
    }

    // ---------- 自动确认“导入文件确认对话框” ----------
    [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern IntPtr FindWindowW(string lpClassName, string lpWindowName);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern bool EnumChildWindows(IntPtr hWndParent, EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern IntPtr SendMessageTimeoutW(IntPtr hWnd, uint Msg, IntPtr wParam,
                                             System.Text.StringBuilder lParam, uint fuFlags,
                                             uint uTimeout, out IntPtr lpdwResult);
    [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern IntPtr SendMessageTimeoutPtr(IntPtr hWnd, uint Msg, IntPtr wParam,
                                               IntPtr lParam, uint fuFlags,
                                               uint uTimeout, out IntPtr lpdwResult);
    const uint WM_GETTEXT = 0x000D;
    const uint WM_GETTEXTLENGTH = 0x000E;
    const uint SMTO_ABORTIFHUNG = 0x0002;

    // 带超时地读窗口文本，避免目标线程不响应时卡死（同步 GetWindowText 会挂住）
    static string SafeWindowText(IntPtr h, int maxLen = 300)
    {
        try
        {
            IntPtr len;
            if (SendMessageTimeoutW(h, WM_GETTEXTLENGTH, IntPtr.Zero, null,
                                    SMTO_ABORTIFHUNG, 80, out len) == IntPtr.Zero)
                return "";
            int n = len.ToInt32();
            if (n <= 0) return "";
            if (n > maxLen) n = maxLen;
            var sb = new System.Text.StringBuilder(n + 2);
            IntPtr res;
            if (SendMessageTimeoutW(h, WM_GETTEXT, (IntPtr)(n + 1), sb,
                                    SMTO_ABORTIFHUNG, 80, out res) == IntPtr.Zero)
                return "";
            return sb.ToString();
        }
        catch { return ""; }
    }
    [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern int GetWindowTextW(IntPtr hWnd, System.Text.StringBuilder lpString, int nMaxCount);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern IntPtr SendMessageW(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern bool PostMessageW(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern int GetClassNameW(IntPtr hWnd, System.Text.StringBuilder lpClassName, int nMaxCount);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern IntPtr GetDlgItem(IntPtr hDlg, int nIDDlgItem);
    const uint BM_CLICK = 0x00F5;
    const uint WM_COMMAND = 0x0111;

    // 诊断用：列出所有顶层窗口及其子控件文本
    static Dictionary<string, object> CmdDumpWindows(Dictionary<string, object> a)
    {
        var list = new List<object>();
        EnumWindows(delegate(IntPtr h, IntPtr l)
        {
            var d = new Dictionary<string, object>();
            var tb = new System.Text.StringBuilder(300);
            GetWindowTextW(h, tb, 300);
            d["hwnd"] = h.ToInt64();
            d["title"] = tb.ToString();
            var kids = new List<string>();
            EnumChildWindows(h, delegate(IntPtr c, IntPtr l2)
            {
                var sb = new System.Text.StringBuilder(200);
                GetWindowTextW(c, sb, 200);
                if (sb.Length > 0) kids.Add(sb.ToString().Trim());
                return true;
            }, IntPtr.Zero);
            d["child_count"] = kids.Count;
            d["children"] = kids.Count > 25 ? kids.GetRange(0, 25) : kids;
            list.Add(d);
            return true;
        }, IntPtr.Zero);
        return Ok(new Dictionary<string, object> { { "count", list.Count }, { "windows", list } });
    }

    // 助手自己发起删除时置 true，用于自动点“是(Y)”，人手删除不受影响
    static volatile bool _deleteDialogAutoConfirm = false;

    // 自动确认的作用域：仅限“本次上传命令执行期间 + 精确匹配的重复对象框”。
    // CAXA 的重复对象对话框通常属于 CAXA 主进程，而不是 helper 本身；因此
    // 不能再用 helper PID 限制，必须依赖标题、正文、目标文件名和上传作用域
    // 四重约束，避免点击其他 CAXA 窗口。
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern int GetDlgCtrlID(IntPtr hWnd);
    const uint BM_GETCHECK = 0x00F0;

    static volatile bool _autoEnabled = false;
    static string _autoMode = "";
    static string _autoRequestId = "";
    static string _autoTargetName = "";
    static DateTime _autoDeadline = DateTime.MinValue;

    static uint CurrentProcessId()
    {
        try { return (uint)System.Diagnostics.Process.GetCurrentProcess().Id; }
        catch { return 0; }
    }

    static Dictionary<string, object> CmdSetAutoConfirm(Dictionary<string, object> a)
    {
        bool enabled = a.ContainsKey("enabled") && Convert.ToBoolean(a["enabled"]);
        if (!enabled)
        {
            _autoEnabled = false;
            _autoMode = "";
            _autoRequestId = "";
            _autoTargetName = "";
            _autoDeadline = DateTime.MinValue;
            ConfirmLog("auto-confirm disabled");
            return Ok(new Dictionary<string, object> { { "enabled", false } });
        }
        string mode = a.ContainsKey("mode") ? Convert.ToString(a["mode"]) : "";
        if (mode != "append_iteration")
        {
            var resp = Err("unsupported auto-confirm mode: " + mode);
            resp["stage"] = "auto_confirm";
            return resp;
        }
        _autoMode = mode;
        _autoRequestId = a.ContainsKey("request_id") ? Convert.ToString(a["request_id"]) : "";
        _autoTargetName = a.ContainsKey("target_name")
            ? Convert.ToString(a["target_name"]).Trim()
            : "";
        _autoDeadline = DateTime.Now.AddSeconds(120);
        _autoEnabled = true;
        ConfirmLog("auto-confirm enable request=" + _autoRequestId
                   + " target=" + _autoTargetName
                   + " pid=" + CurrentProcessId());
        return Ok(new Dictionary<string, object>
        {
            { "enabled", true },
            { "mode", _autoMode },
            { "request_id", _autoRequestId },
            { "target_name", _autoTargetName },
            { "process_id", CurrentProcessId() },
            { "dialog_fingerprint_scoped", true }
        });
    }

    static bool IsAutoTargetAllowed(uint pid)
    {
        // 只允许本 helper 自己的窗口；2011 现场日志确认重复框 PID 与 helper
        // 相同。即使文本指纹一致，也绝不跨进程点击用户手动发起的对话框。
        if (!_autoEnabled || !_uploadInProgress) return false;
        if (DateTime.Now > _autoDeadline) return false;
        if (_autoMode != "append_iteration") return false;
        return pid != 0 && pid == CurrentProcessId();
    }

    static string SafeWindowTitle(IntPtr hWnd)
    {
        try
        {
            var tb = new System.Text.StringBuilder(300);
            GetWindowTextW(hWnd, tb, 300);
            return tb.ToString();
        }
        catch { return ""; }
    }

    // 按子控件文本找“对象已经存在提示框”；多处命中或进程不符时一律不点。
    static IntPtr FindDupDialog(out string childTexts, out uint ownerPid)
    {
        IntPtr found = IntPtr.Zero;
        string texts = "";
        uint pid = 0;
        int matches = 0;
        EnumWindows(delegate(IntPtr h, IntPtr l)
        {
            if (SafeWindowTitle(h).Trim() != "对象已经存在提示框") return true;
            var parts = new List<string>();
            EnumChildWindows(h, delegate(IntPtr c, IntPtr l2)
            {
                string s = SafeWindowText(c, 200);
                if (s.Length > 0) parts.Add(s.Trim());
                return true;
            }, IntPtr.Zero);
            string joined = string.Join(" | ", parts.ToArray());
            if (!joined.Contains("在数据库中已经存在") ||
                !joined.Contains("追加新的版次"))
                return true;
            if (!string.IsNullOrWhiteSpace(_autoTargetName) &&
                !joined.Contains(_autoTargetName))
                return true;
            uint hpid;
            GetWindowThreadProcessId(h, out hpid);
            if (!IsAutoTargetAllowed(hpid)) return true;
            matches++;
            if (matches == 1)
            {
                found = h;
                texts = joined;
                pid = hpid;
            }
            return true;
        }, IntPtr.Zero);
        if (matches != 1)
        {
            found = IntPtr.Zero;
            if (matches > 1) texts = "";
            pid = 0;
        }
        childTexts = texts;
        ownerPid = pid;
        return found;
    }

    // CAXA 客户端已登录同一账号时，helper 再以该账号登录会弹出固定提示：
    // “同一个用户已经登陆 / 错误号：0x83060607”。只匹配这两个文本同时存在
    // 且标题为“提示框”的唯一窗口，避免影响其他错误提示。
    static IntPtr FindSameUserLoginDialog(out string childTexts, out uint ownerPid)
    {
        IntPtr found = IntPtr.Zero;
        string texts = "";
        uint pid = 0;
        int matches = 0;
        EnumWindows(delegate(IntPtr h, IntPtr l)
        {
            if (SafeWindowTitle(h).Trim() != "提示框") return true;
            var parts = new List<string>();
            EnumChildWindows(h, delegate(IntPtr c, IntPtr l2)
            {
                string s = SafeWindowText(c, 300);
                if (s.Length > 0) parts.Add(s.Trim());
                return true;
            }, IntPtr.Zero);
            string joined = string.Join(" | ", parts.ToArray());
            if (!joined.Contains("同一个用户已经登") ||
                !joined.ToUpperInvariant().Contains("0X83060607"))
                return true;
            matches++;
            if (matches == 1)
            {
                found = h;
                texts = joined;
                GetWindowThreadProcessId(h, out pid);
            }
            return true;
        }, IntPtr.Zero);
        if (matches != 1)
        {
            found = IntPtr.Zero;
            if (matches > 1) texts = "";
            pid = 0;
        }
        childTexts = texts;
        ownerPid = pid;
        return found;
    }

    static void StartDialogAutoConfirmer()
    {
        ConfirmLog("auto-confirmer thread starting");
        var t = new System.Threading.Thread(() =>
        {
            ConfirmLog("auto-confirmer thread started（默认空闲，等待 set_auto_confirm）");
            IntPtr lastDel = IntPtr.Zero;
            IntPtr lastDup = IntPtr.Zero;
            IntPtr lastImport = IntPtr.Zero;
            IntPtr lastSameUserLogin = IntPtr.Zero;
            int ticks = 0;
            while (true)
            {
                ticks++;
                bool beat = (ticks % 40 == 0);
                try
                {
                    // 0) 删除确认框（仅助手自己发起删除时处理）
                    IntPtr del = FindWindowW(null, "协同管理");
                    if (del != IntPtr.Zero && del != lastDel)
                    {
                        lastDel = del;
                        uint delPid;
                        GetWindowThreadProcessId(del, out delPid);
                        if (_deleteDialogAutoConfirm && delPid == CurrentProcessId())
                        {
                            ConfirmLog("发现删除确认框: " + DescribeChildren(del));
                            bool yes = ClickChildContaining(del, "是");
                            ConfirmLog("删除确认“是”点击=" + yes);
                        }
                    }
                    if (del == IntPtr.Zero) lastDel = IntPtr.Zero;

                    // 0.5) 同一账号已登录提示：精确匹配文本和错误码后点“确定”。
                    string sameUserTexts;
                    uint sameUserPid;
                    IntPtr sameUser = FindSameUserLoginDialog(
                        out sameUserTexts, out sameUserPid);
                    if (sameUser != IntPtr.Zero && sameUser != lastSameUserLogin)
                    {
                        lastSameUserLogin = sameUser;
                        if (_loginInProgress && sameUserPid == CurrentProcessId())
                        {
                            bool okBtn = ClickDialogPrimaryButton(sameUser, "确定");
                            ConfirmLog("同一账号已登录提示框 控件=" + sameUserTexts
                                       + " 确定点击=" + okBtn);
                        }
                        else
                        {
                            ConfirmLog("忽略非当前 login 作用域的同一账号提示 pid="
                                       + sameUserPid + " 控件=" + sameUserTexts);
                        }
                    }
                    if (sameUser == IntPtr.Zero) lastSameUserLogin = IntPtr.Zero;

                    // 1) 重复对象对话框：只在本次请求窗口内、且属于已锁定进程时处理
                    string dupTexts;
                    uint dupPid;
                    IntPtr dup = FindDupDialog(out dupTexts, out dupPid);
                    if (beat)
                    {
                        ConfirmLog("heartbeat ticks=" + ticks + " auto=" + _autoEnabled
                                   + " dup=" + dup
                                   + (dup != IntPtr.Zero ? " pid=" + dupPid + " 控件=" + dupTexts : ""));
                    }
                    if (dup != IntPtr.Zero)
                    {
                        if (dup != lastDup)
                        {
                            lastDup = dup;
                            ConfirmLog("发现重复对象对话框 hwnd=" + dup + " pid=" + dupPid
                                       + " 标题=" + SafeWindowTitle(dup) + " 控件=" + dupTexts);
                            bool selected = SelectOptionAndVerify(dup, "追加新的版次");
                            ConfirmLog("“在现有版本上追加新的版次”选中=" + selected);
                            if (!selected)
                            {
                                // 选项不明或没能确认选中：不点“确定”
                                System.Threading.Thread.Sleep(300);
                                continue;
                            }
                            System.Threading.Thread.Sleep(120);
                            bool all = SetCheckBoxChecked(dup, "应用到所有");
                            ConfirmLog("“应用到所有”点击=" + all);
                        }
                        bool okBtn = ClickButton(dup, "确定");
                        ConfirmLog("重复对象对话框“确定”点击=" + okBtn + " hwnd=" + dup);
                    }
                    else
                    {
                        lastDup = IntPtr.Zero;
                    }

                    // 2) “导入文件确认对话框”：同样只在请求作用域内处理
                    IntPtr dlg = FindWindowW(null, "导入文件确认对话框");
                    if (dlg != IntPtr.Zero && dlg != lastImport)
                    {
                        uint dlgPid;
                        GetWindowThreadProcessId(dlg, out dlgPid);
                        if (IsAutoTargetAllowed(dlgPid))
                        {
                            lastImport = dlg;
                            string importChildren = DescribeChildren(dlg);
                            bool okBtn = ClickDialogPrimaryButton(dlg, "确定");
                            ConfirmLog("“导入文件确认对话框” pid=" + dlgPid
                                       + " 控件=" + importChildren
                                       + " 确定点击=" + okBtn);
                        }
                    }
                    if (dlg == IntPtr.Zero) lastImport = IntPtr.Zero;
                }
                catch { }
                System.Threading.Thread.Sleep(250);
            }
        });
        t.IsBackground = true;
        t.Start();
    }

    static AutomationElement FindAutomationElement(
        IntPtr parent, ControlType controlType, string fragment)
    {
        try
        {
            AutomationElement root = AutomationElement.FromHandle(parent);
            var byType = new PropertyCondition(
                AutomationElement.ControlTypeProperty, controlType);
            AutomationElementCollection matches = root.FindAll(
                TreeScope.Descendants, byType);
            AutomationElement found = null;
            foreach (AutomationElement item in matches)
            {
                string name = "";
                try { name = item.Current.Name ?? ""; } catch { }
                if (!name.Contains(fragment)) continue;
                if (found != null) return null;
                found = item;
            }
            return found;
        }
        catch { return null; }
    }

    static bool SetCheckBoxChecked(IntPtr parent, string fragment)
    {
        AutomationElement item = FindAutomationElement(
            parent, ControlType.CheckBox, fragment);
        if (item == null) return ClickUniqueChild(parent, fragment);
        try
        {
            var toggle = item.GetCurrentPattern(TogglePattern.Pattern)
                as TogglePattern;
            if (toggle == null) return false;
            if (toggle.Current.ToggleState != ToggleState.On)
                toggle.Toggle();
            System.Threading.Thread.Sleep(100);
            return toggle.Current.ToggleState == ToggleState.On;
        }
        catch { return false; }
    }

    static bool ClickButton(IntPtr parent, string fragment)
    {
        AutomationElement item = FindAutomationElement(
            parent, ControlType.Button, fragment);
        if (item == null) return ClickUniqueChild(parent, fragment);
        try
        {
            var invoke = item.GetCurrentPattern(InvokePattern.Pattern)
                as InvokePattern;
            if (invoke == null) return false;
            invoke.Invoke();
            return true;
        }
        catch { return false; }
    }

    // 选中选项并回读勾选状态；返回 false 时调用方不得点“确定”。
    static bool SelectOptionAndVerify(IntPtr parent, string fragment)
    {
        AutomationElement radio = FindAutomationElement(
            parent, ControlType.RadioButton, fragment);
        if (radio != null)
        {
            try
            {
                var selection = radio.GetCurrentPattern(
                    SelectionItemPattern.Pattern) as SelectionItemPattern;
                if (selection != null)
                {
                    if (!selection.Current.IsSelected) selection.Select();
                    System.Threading.Thread.Sleep(100);
                    if (selection.Current.IsSelected) return true;
                }
            }
            catch { }
        }
        var matches = new List<IntPtr>();
        EnumChildWindows(parent, (h, l) =>
        {
            string s = SafeWindowText(h, 300);
            if (!string.IsNullOrEmpty(s) && s.Contains(fragment)) matches.Add(h);
            return true;
        }, IntPtr.Zero);
        if (matches.Count != 1) return false;
        IntPtr target = matches[0];
        try
        {
            IntPtr res;
            if (SendMessageTimeoutPtr(target, BM_CLICK, IntPtr.Zero, IntPtr.Zero,
                                      SMTO_ABORTIFHUNG, 500, out res) == IntPtr.Zero)
                return false;
        }
        catch { return false; }
        System.Threading.Thread.Sleep(100);
        try
        {
            IntPtr state;
            if (SendMessageTimeoutPtr(target, BM_GETCHECK, IntPtr.Zero, IntPtr.Zero,
                                      SMTO_ABORTIFHUNG, 300, out state) == IntPtr.Zero)
                return false;
            return state.ToInt64() == 1;
        }
        catch { return false; }
    }

    // 只在唯一命中时点击（“确定/应用到所有”）。
    static bool ClickUniqueChild(IntPtr parent, string fragment)
    {
        var matches = new List<IntPtr>();
        EnumChildWindows(parent, (h, l) =>
        {
            string s = SafeWindowText(h, 300);
            if (!string.IsNullOrEmpty(s) && s.Contains(fragment)) matches.Add(h);
            return true;
        }, IntPtr.Zero);
        if (matches.Count != 1) return false;
        try
        {
            IntPtr res;
            SendMessageTimeoutPtr(matches[0], BM_CLICK, IntPtr.Zero, IntPtr.Zero,
                                  SMTO_ABORTIFHUNG, 500, out res);
        }
        catch { return false; }
        return true;
    }

    static string NormalizeControlText(string text)
    {
        return (text ?? "")
            .Replace("&", "")
            .Replace("（", "(")
            .Replace("）", ")")
            .Replace(" ", "")
            .Trim();
    }

    static bool IsButtonControl(IntPtr h)
    {
        try
        {
            var sb = new System.Text.StringBuilder(80);
            if (GetClassNameW(h, sb, sb.Capacity) <= 0) return false;
            return string.Equals(
                sb.ToString().Trim(), "Button", StringComparison.OrdinalIgnoreCase
            );
        }
        catch { return false; }
    }

    // 导入确认框的确定按钮可能显示为“确定(O)”或由默认按钮处理。
    // 先按 Button 类 + 归一化文本唯一命中；否则尝试 IDOK / WM_COMMAND。
    static bool ClickDialogPrimaryButton(IntPtr dialog, string fragment)
    {
        var matches = new List<IntPtr>();
        string target = NormalizeControlText(fragment);
        EnumChildWindows(dialog, (h, l) =>
        {
            if (!IsButtonControl(h)) return true;
            string text = NormalizeControlText(SafeWindowText(h, 300));
            if (text.StartsWith(target, StringComparison.OrdinalIgnoreCase))
                matches.Add(h);
            return true;
        }, IntPtr.Zero);

        if (matches.Count == 1)
        {
            try
            {
                IntPtr res;
                SendMessageTimeoutPtr(
                    matches[0], BM_CLICK, IntPtr.Zero, IntPtr.Zero,
                    SMTO_ABORTIFHUNG, 1000, out res
                );
                return true;
            }
            catch { }
        }

        IntPtr defaultButton = GetDlgItem(dialog, 1); // IDOK
        if (defaultButton != IntPtr.Zero && IsButtonControl(defaultButton))
        {
            try
            {
                IntPtr res;
                SendMessageTimeoutPtr(
                    defaultButton, BM_CLICK, IntPtr.Zero, IntPtr.Zero,
                    SMTO_ABORTIFHUNG, 1000, out res
                );
                return true;
            }
            catch { }
        }
        try
        {
            return PostMessageW(
                dialog, WM_COMMAND, (IntPtr)1, IntPtr.Zero
            );
        }
        catch { return false; }
    }

    // 找到子控件（文本包含指定片段）并点击，返回是否点中
    static bool ClickChildContaining(IntPtr parent, string fragment)
    {
        IntPtr target = IntPtr.Zero;
        EnumChildWindows(parent, (h, l) =>
        {
            string s = SafeWindowText(h, 300);
            if (!string.IsNullOrEmpty(s) && s.Contains(fragment))
            {
                target = h;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        if (target != IntPtr.Zero)
        {
            try
            {
                IntPtr res;
                SendMessageTimeoutPtr(target, BM_CLICK, IntPtr.Zero, IntPtr.Zero,
                                      SMTO_ABORTIFHUNG, 500, out res);
            }
            catch { }
            return true;
        }
        return false;
    }

    // 自动确认线程的简单日志（排查用）
    static readonly object _logLock = new object();
    // 枚举对话框里的可见子控件文本（写日志用）
    static string DescribeChildren(IntPtr parent)
    {
        var parts = new List<string>();
        EnumChildWindows(parent, (h, l) =>
        {
            string s = SafeWindowText(h, 300);
            if (!string.IsNullOrEmpty(s))
            {
                var cls = new System.Text.StringBuilder(80);
                try { GetClassNameW(h, cls, cls.Capacity); } catch { }
                parts.Add(
                    "[" + cls.ToString() + "#" + GetDlgCtrlID(h) + "] " +
                    s.Trim()
                );
            }
            return true;
        }, IntPtr.Zero);
        return string.Join(" | ", parts.ToArray());
    }

    static void ConfirmLog(string line)
    {
        try
        {
            lock (_logLock)
            {
                File.AppendAllText(
                    Path.Combine(Path.GetTempPath(), "caxa_autoconfirmer.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + " " + line + Environment.NewLine,
                    new UTF8Encoding(false));
            }
        }
        catch { }
    }
}
