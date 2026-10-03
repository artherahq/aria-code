# Install the standalone Aria Code CLI without Python, Node.js, or npm.
$ErrorActionPreference = 'Stop'

$arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($arch -ne 'X64') { throw "Unsupported Windows architecture: $arch (the release currently ships Windows x64 only)." }

$version = if ($env:ARIA_CODE_VERSION) { $env:ARIA_CODE_VERSION } else { 'latest' }
if ($version -eq 'latest') {
    $base = 'https://github.com/artheras/aria-code/releases/latest/download'
} elseif ($version -match '^v\d+\.\d+\.\d+$') {
    $base = "https://github.com/artheras/aria-code/releases/download/$version"
} else {
    throw 'ARIA_CODE_VERSION must look like v0.55.0.'
}

$asset = 'aria-code-windows-x64.exe'
$installDir = if ($env:ARIA_CODE_INSTALL_DIR) { $env:ARIA_CODE_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'AriaCode\bin' }
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("aria-code-install-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempDir | Out-Null

try {
    $checksums = Join-Path $tempDir 'SHA256SUMS'
    $binary = Join-Path $tempDir $asset
    Write-Host "Downloading $asset..."
    Invoke-WebRequest -UseBasicParsing -Uri "$base/SHA256SUMS" -OutFile $checksums
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$asset" -OutFile $binary

    $line = Get-Content $checksums | Where-Object { $_ -match "^[a-fA-F0-9]{64}\s+\*?$([regex]::Escape($asset))$" } | Select-Object -First 1
    if (-not $line) { throw "Checksum for $asset is missing from this release." }
    $expected = ($line -split '\s+')[0].ToLowerInvariant()
    $actual = (Get-FileHash -Algorithm SHA256 -Path $binary).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "Checksum mismatch for $asset." }
    & $binary --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Downloaded binary failed its version check.' }

    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    $destination = Join-Path $installDir 'aria-code.exe'
    Copy-Item -Force $binary $destination
    Copy-Item -Force $binary (Join-Path $installDir 'aria.exe')

    if (-not $env:ARIA_CODE_INSTALL_DIR) {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $entries = @($userPath -split ';' | Where-Object { $_ })
        if ($entries -notcontains $installDir) {
            [Environment]::SetEnvironmentVariable('Path', (($entries + $installDir) -join ';'), 'User')
            Write-Host 'Added AriaCode\bin to your user PATH for future terminals.'
        }
        $env:Path = "$installDir;$env:Path"
    }
    Write-Host "Installed $destination"
    Write-Host "Run it now: & '$destination' --help"
} finally {
    Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue
}
