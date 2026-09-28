param([switch] $Detached)

$ErrorActionPreference = 'Stop'

function FullPath([string] $Path) {
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function SamePath([string] $Left, [string] $Right) {
    return [string]::Equals((FullPath $Left), (FullPath $Right), [System.StringComparison]::OrdinalIgnoreCase)
}

try {
    if ($Detached) { Start-Sleep -Seconds 1 }
    $userProfile = FullPath $env:USERPROFILE
    $codexHome = if ($env:CODEX_HOME) { FullPath $env:CODEX_HOME } else { FullPath (Join-Path $userProfile '.codex') }
    $skillsRoot = FullPath (Join-Path $codexHome 'skills')
    $target = FullPath (Join-Path $skillsRoot 'video-montage')

    if (-not (SamePath (Split-Path $target -Parent) $skillsRoot)) {
        throw "Unsafe installation path: $target"
    }
    if (Test-Path -LiteralPath $target) {
        $item = Get-Item -LiteralPath $target -Force
        if (-not $item.PSIsContainer -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw "Installation path is not a regular directory: $target"
        }
        $marker = Join-Path $target 'SKILL.md'
        $executor = Join-Path $target 'components\executor\scripts\three_suite_ff.py'
        if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or
            -not (Test-Path -LiteralPath $executor -PathType Leaf) -or
            -not (Select-String -LiteralPath $marker -Pattern '^name: video-montage$' -Quiet)) {
            throw "Installation path does not contain a video-montage installation: $target"
        }
    }

    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Output "Removed installation: $target"
    }
    Write-Output 'video-montage uninstall complete.'
} catch {
    Write-Error $_
    exit 1
}
