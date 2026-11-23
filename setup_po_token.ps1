$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$providerPath = Join-Path $PSScriptRoot '.local\bgutil-ytdlp-pot-provider'
if (-not (Test-Path -LiteralPath $providerPath)) {
    git clone --depth 1 --single-branch --branch 2.0.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git $providerPath
    if ($LASTEXITCODE -ne 0) { throw 'Cannot download PO Token Provider.' }
}
Push-Location (Join-Path $providerPath 'server')
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
    & .\node_modules\.bin\tsc.cmd
    if ($LASTEXITCODE -ne 0) { throw 'TypeScript build failed.' }
} finally {
    Pop-Location
}
& .\.venv\Scripts\python.exe -m pip install bgutil-ytdlp-pot-provider==2.0.2
if ($LASTEXITCODE -ne 0) { throw 'Cannot install provider plugin.' }
Write-Host 'PO Token Provider ready. Restart the bot to activate it.'
