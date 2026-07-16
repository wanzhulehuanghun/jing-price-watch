function Find-JpwPythonRuntime {
    $candidates = @()
    $bundled = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $bundled) {
        $candidates += [pscustomobject]@{ File = $bundled; Prefix = @() }
    }
    $pythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pythonCommand) {
        $candidates += [pscustomobject]@{ File = $pythonCommand.Source; Prefix = @() }
    }
    $pyCommand = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pyCommand) {
        $candidates += [pscustomobject]@{ File = $pyCommand.Source; Prefix = @("-3") }
    }

    foreach ($candidate in $candidates) {
        try {
            $prefix = @($candidate.Prefix)
            & $candidate.File $prefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { }
    }
    return $null
}

function Find-JpwNodeRuntime {
    $candidates = @()
    $bundled = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    if (Test-Path -LiteralPath $bundled) { $candidates += $bundled }
    $nodeCommand = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($nodeCommand) { $candidates += $nodeCommand.Source }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        try {
            & $candidate -e "const major=Number(process.versions.node.split('.')[0]); process.exit(major >= 22 && typeof WebSocket === 'function' ? 0 : 1)" *> $null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { }
    }
    return $null
}

function Find-JpwEdgeRuntime {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
    ) | Where-Object { $_ }
    $edgeCommand = Get-Command msedge.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($edgeCommand) { $candidates += $edgeCommand.Source }
    return $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

function Get-JpwRuntime {
    $python = Find-JpwPythonRuntime
    $node = Find-JpwNodeRuntime
    $edge = Find-JpwEdgeRuntime
    $missing = @()
    if (-not $python) { $missing += "Python 3.11+" }
    if (-not $node) { $missing += "Node.js 22+" }
    if (-not $edge) { $missing += "Microsoft Edge" }
    if ($missing.Count -gt 0) {
        throw "Missing or unsupported runtime: $($missing -join ', ')."
    }
    return [pscustomobject]@{
        Python = $python.File
        PythonPrefix = @($python.Prefix)
        Node = $node
        Edge = $edge
    }
}
