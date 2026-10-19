param([ValidateSet('onefile', 'onedir')][string]$Mode = 'onefile', [switch]$HealthbarsPreview)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $python = Join-Path $PSScriptRoot '.venv-build\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Create .venv-build and install requirements-build.txt first.' }
    $artifactName = if ($HealthbarsPreview) { 'SC2-Ultrawide-Healthbars-Preview' } else { 'SC2-Ultrawide-Portable' }
    $artifactDirectory = if ($HealthbarsPreview) { 'healthbars-preview' } else { $Mode }
    & $python -m PyInstaller --noconfirm --clean "--$Mode" --windowed --noupx --name $artifactName --distpath "dist\$artifactDirectory" --workpath "build\$artifactDirectory" --specpath build sc2_ultrawide_gui.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
} finally { Pop-Location }
