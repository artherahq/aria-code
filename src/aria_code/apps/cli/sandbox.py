"""Sandbox execution module for Aria Code."""
import subprocess
import os

def run_in_docker_sandbox(command: str, cwd: str = None, timeout: int = 120, image: str = "python:3.11-slim") -> subprocess.CompletedProcess:
    """Run a shell command securely inside an isolated Docker container.
    
    This mounts the current workspace so scripts can operate on files,
    but prevents access to the host's system files and network interfaces
    if configured with --network none (optional).
    """
    workspace = cwd or os.getcwd()
    
    # We use docker run with a volume mount to the workspace.
    # --rm: remove container after exit
    # -v: mount workspace to /workspace
    # -w: set working directory to /workspace
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{workspace}:/workspace",
        "-w", "/workspace",
        # "--network", "none", # Uncomment to completely isolate network
        image,
        "sh", "-c", command
    ]
    
    return subprocess.run(
        docker_cmd,
        capture_output=True,
        text=True,
        timeout=timeout
    )
