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
    $target = FullPath (Join-Path $userProfile 'video-montage')
    $skill = FullPath (Join-Path $target 'skill\video-montage')
    $codexHome = if ($env:CODEX_HOME) { FullPath $env:CODEX_HOME } else { FullPath (Join-Path $userProfile '.codex') }
    $roots = @((Join-Path $codexHome 'skills'), (Join-Path $userProfile '.agents\skills'))

    if (-not (SamePath (Split-Path $target -Parent) $userProfile)) {
        throw "Unsafe installation path: $target"
    }
    if (Test-Path -LiteralPath $target) {
        $item = Get-Item -LiteralPath $target -Force
        if (-not $item.PSIsContainer -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw "Installation path is not a regular directory: $target"
        }
        $marker = Join-Path $skill 'SKILL.md'
        $executor = Join-Path $target 'components\executor\scripts\three_suite_ff.py'
        if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or
            -not (Test-Path -LiteralPath $executor -PathType Leaf) -or
            -not (Select-String -LiteralPath $marker -Pattern '^name: video-montage$' -Quiet)) {
            throw "Installation path does not contain a video-montage installation: $target"
        }
    }

    foreach ($root in $roots) {
        $link = Join-Path $root 'video-montage'
        if (-not (Test-Path -LiteralPath $link)) { continue }
        $item = Get-Item -LiteralPath $link -Force
        if ($item.LinkType -ne 'Junction') { continue }
        $destination = [string] $item.Target
        if (-not [System.IO.Path]::IsPathRooted($destination)) {
            $destination = Join-Path $root $destination
        }
        if (SamePath $destination $skill) {
            [System.IO.Directory]::Delete($link)
            Write-Output "Removed skill: $link"
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
