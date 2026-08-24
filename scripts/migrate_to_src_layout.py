#!/usr/bin/env python3
import os
import tomllib as toml
import shutil

def main():
    print("=== Aria Code Src-Layout Migration Tool ===")
    print("This script helps you safely migrate your code to the src/aria_code layout.")
    print("It will create the directory and move the modules, but you will need to")
    print("run an AST-based tool (like rope or refactor) to fix imports.")
    
    if not os.path.exists("pyproject.toml"):
        print("Run this from the project root!")
        return

    # Create src layout
    os.makedirs("src/aria_code", exist_ok=True)
    
    with open("pyproject.toml", "rb") as f:
        config = toml.load(f)
        
    modules = config.get("tool", {}).get("setuptools", {}).get("py-modules", [])
    packages = config.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {}).get("include", [])
    
    print(f"Found {len(modules)} top-level modules and {len(packages)} packages.")
    
    # Move files
    moves = []
    for mod in modules:
        py_file = f"{mod}.py"
        if os.path.exists(py_file):
            moves.append((py_file, f"src/aria_code/{py_file}"))
            
    for pkg in packages:
        pkg_dir = pkg.replace("*", "")
        if os.path.exists(pkg_dir) and pkg_dir != "src":
            moves.append((pkg_dir, f"src/aria_code/{pkg_dir}"))
            
    print(f"\nReady to move {len(moves)} items into src/aria_code/")
    
    # Generate bash script
    with open("scripts/do_move.sh", "w") as f:
        f.write("#!/bin/bash\n")
        f.write("mkdir -p src/aria_code\n")
        f.write("touch src/aria_code/__init__.py\n")
        for src, dest in moves:
            f.write(f"git mv {src} {dest} 2>/dev/null || mv {src} {dest}\n")
            
    os.chmod("scripts/do_move.sh", 0o755)
    print("\nGenerated scripts/do_move.sh. Run it to move the files.")
    print("WARNING: This will break imports. You must update imports (e.g. `import agents` -> `from aria_code import agents`)")

if __name__ == "__main__":
    main()
