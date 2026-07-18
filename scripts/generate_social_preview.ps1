param(
    [string]$OutputPath = "docs/assets/social-preview.png"
)

Add-Type -AssemblyName System.Drawing

$width = 1280
$height = 640
$bitmap = New-Object System.Drawing.Bitmap($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

function Color([string]$hex) {
    return [System.Drawing.ColorTranslator]::FromHtml($hex)
}

function Font([float]$size, [System.Drawing.FontStyle]$style = [System.Drawing.FontStyle]::Regular) {
    return New-Object System.Drawing.Font("Segoe UI", $size, $style, [System.Drawing.GraphicsUnit]::Pixel)
}

try {
    $graphics.Clear((Color "#0c1118"))
    $white = New-Object System.Drawing.SolidBrush((Color "#f4f7fb"))
    $muted = New-Object System.Drawing.SolidBrush((Color "#b5c0cf"))
    $teal = New-Object System.Drawing.SolidBrush((Color "#3dd7b0"))
    $panel = New-Object System.Drawing.SolidBrush((Color "#121923"))
    $border = New-Object System.Drawing.Pen((Color "#2b3544"), 1)
    $tealPen = New-Object System.Drawing.Pen((Color "#3dd7b0"), 5)

    $graphics.DrawString("IC Copilot", (Font 25 Bold), $white, 72, 54)
    $graphics.DrawRectangle((New-Object System.Drawing.Pen((Color "#2e6157"), 1)), 1012, 48, 196, 42)
    $graphics.DrawString("OPEN SOURCE", (Font 14 Bold), $teal, 1041, 59)

    $graphics.DrawString("From source event to an", (Font 50 Bold), $white, 72, 152)
    $graphics.DrawString("auditable investment thesis.", (Font 50 Bold), $white, 72, 209)
    $graphics.DrawString(
        "SEC and issuer evidence, causal bridges, peer read-through, valuation,",
        (Font 20 Regular), $muted, 74, 301
    )
    $graphics.DrawString(
        "counter-thesis, and monitor rules in one IC-ready workflow.",
        (Font 20 Regular), $muted, 74, 334
    )

    $graphics.FillRectangle($panel, 841, 139, 367, 254)
    $graphics.DrawRectangle($border, 841, 139, 367, 254)
    $graphics.DrawLine($tealPen, 843, 141, 843, 390)
    $graphics.DrawString("DECISION DISCIPLINE", (Font 13 Bold), $muted, 874, 171)
    $graphics.DrawString("It can say", (Font 27 Bold), $white, 874, 213)
    $graphics.DrawString("No convincing thesis yet.", (Font 27 Bold), $white, 874, 250)
    $graphics.DrawString("LLMs synthesize validated evidence.", (Font 15 Regular), $muted, 874, 309)
    $graphics.DrawString("Deterministic gates control promotion.", (Font 15 Regular), $muted, 874, 335)

    $graphics.DrawLine($border, 72, 494, 1208, 494)
    $steps = @("Sources", "Claims", "Drivers", "Valuation", "Thesis", "Monitor")
    for ($index = 0; $index -lt $steps.Count; $index++) {
        $x = 72 + ($index * 190)
        $graphics.FillEllipse($teal, $x, 535, 9, 9)
        $graphics.DrawString($steps[$index], (Font 16 Regular), $muted, $x + 17, 527)
    }
    $graphics.DrawString("ic-copilot.streamlit.app", (Font 15 Regular), $muted, 72, 590)

    $resolved = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputPath))
    $directory = [System.IO.Path]::GetDirectoryName($resolved)
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    $bitmap.Save($resolved, [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Output "Wrote $resolved"
}
finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
