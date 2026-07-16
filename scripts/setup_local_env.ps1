param(
    [string]$OutputPath = ".env.local"
)

$ErrorActionPreference = "Stop"

function Read-PlainSecret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
    }
}

Write-Host "This writes local keys to $OutputPath. That file is gitignored."
Write-Host "Press Enter to leave an optional value blank."

$values = [ordered]@{
    SEC_USER_AGENT = Read-Host "SEC user agent, e.g. Your Name email@example.com"
    ALPHAVANTAGE_API_KEY = Read-PlainSecret "Alpha Vantage API key"
    FINNHUB_API_KEY = Read-PlainSecret "Finnhub API key"
    FRED_API_KEY = Read-PlainSecret "FRED API key"
    BEA_API_KEY = Read-PlainSecret "BEA API key"
    CENSUS_API_KEY = Read-PlainSecret "Census API key"
    ENABLE_DEFAULT_MACRO = "true"
    GLOBAL_MACRO_MODE = "false"
    ENABLE_GDELT = "false"
}

$lines = @(
    "# Local secrets for US Equity Research Radar",
    "# This file is intentionally ignored by git.",
    ""
)

foreach ($key in $values.Keys) {
    $value = [string]$values[$key]
    if ($value.Length -eq 0) {
        continue
    }
    $escaped = $value.Replace('\', '\\').Replace('"', '\"')
    $lines += "$key=""$escaped"""
}

Set-Content -LiteralPath $OutputPath -Value $lines -Encoding UTF8
Write-Host "Wrote $OutputPath. Restart Streamlit/server.py so the app reloads keys."
