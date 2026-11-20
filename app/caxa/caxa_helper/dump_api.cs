using System;
using System.Linq;
using System.Reflection;

class DumpApi
{
    static void DumpType(Type t, string indent)
    {
        string kind = t.IsInterface ? "interface" : t.IsEnum ? "enum" : t.IsClass ? "class" : "type";
        Console.WriteLine(indent + kind + " " + t.FullName);
        if (t.IsEnum)
        {
            foreach (string name in Enum.GetNames(t))
                Console.WriteLine(indent + "  " + name + " = " + Convert.ToInt64(Enum.Parse(t, name)));
            return;
        }
        foreach (MethodInfo m in t.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly)
                                 .OrderBy(m => m.Name))
        {
            try
            {
                string ret = m.ReturnType.FullName;
                string pars = string.Join(", ", m.GetParameters().Select(p =>
                    (p.IsOut ? "out " : p.ParameterType.IsByRef ? "ref " : "") +
                    p.ParameterType.FullName + " " + p.Name));
                Console.WriteLine(indent + "  " + ret + " " + m.Name + "(" + pars + ")");
            }
            catch (Exception e)
            {
                Console.WriteLine(indent + "  ? " + m.Name + " (" + e.Message + ")");
            }
        }
        foreach (PropertyInfo p in t.GetProperties(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly))
        {
            Console.WriteLine(indent + "  prop " + p.PropertyType.FullName + " " + p.Name +
                              (p.CanRead ? " {get;}" : "") + (p.CanWrite ? " {set;}" : ""));
        }
        foreach (FieldInfo f in t.GetFields(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
        {
            Console.WriteLine(indent + "  field " + f.FieldType.FullName + " " + f.Name);
        }
    }

    static int Main(string[] args)
    {
        if (args.Length == 0)
        {
            Console.Error.WriteLine("usage: dump_api.exe <assembly.dll> [typeFilter]");
            return 1;
        }
        try
        {
            Assembly asm = Assembly.LoadFrom(args[0]);
            var types = asm.GetExportedTypes().OrderBy(t => t.FullName);
            foreach (Type t in types)
            {
                if (args.Length > 1 && t.FullName.IndexOf(args[1], StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                DumpType(t, "");
            }
            return 0;
        }
        catch (Exception e)
        {
            Console.Error.WriteLine(e.ToString());
            return 2;
        }
    }
}
