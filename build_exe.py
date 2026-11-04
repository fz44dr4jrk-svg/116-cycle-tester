import os
import subprocess
import sys

SEP = ";" if os.name == "nt" else ":"


def find_phidget_dll():
    import Phidget22

    pkg = os.path.dirname(Phidget22.__file__)
    candidates = [
        os.path.join(pkg, ".libs", "phidget22.dll"),
        os.path.join(pkg, "phidget22.dll"),
        os.path.join(pkg, ".libs", "libphidget22.so"),
        os.path.join(pkg, "libphidget22.so"),
    ]
    return next((p for p in candidates if os.path.isfile(p)), None)


def main():
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        "L16CycleTester",
        "server.py",
        "--add-data",
        f"static{SEP}static",
        "--collect-all",
        "Phidget22",
    ]
    dll = find_phidget_dll()
    if dll:
        print(f"bundling native library: {dll}")
        cmd += ["--add-binary", f"{dll}{SEP}."]
    else:
        print("WARNING: phidget22 native library not found in the Phidget22 package")
    subprocess.check_call(cmd)
    print("built dist/L16CycleTester.exe")


if __name__ == "__main__":
    main()
