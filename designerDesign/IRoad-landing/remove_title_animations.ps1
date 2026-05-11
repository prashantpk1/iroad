$files = Get-ChildItem -Recurse -Include *.html | Where-Object { $_.FullName -notmatch '\.history\\' }
foreach ($file in $files) {
    $text = Get-Content -Raw $file -Encoding UTF8
    $new = [regex]::Replace($text, '<h([123])\b([^>]*)>', {
        param($m)
        $tag = $m.Groups[1].Value
        $attrs = $m.Groups[2].Value
        $attrs = [regex]::Replace($attrs, '\s*\bdata-cursor="-opaque"\b', '')
        $attrs = [regex]::Replace($attrs, '\s*\bdata-wow-delay="[^"]*"\b', '')
        $attrs = [regex]::Replace($attrs, 'class="([^"]*)"', {
            param($mc)
            $classes = $mc.Groups[1].Value -split '\s+'
            $filtered = $classes | Where-Object { $_ -and $_ -notin @('wow','fadeInUp','text-anime-style-1','text-anime-style-2','text-anime-style-3','text-effect') }
            if ($filtered.Count -gt 0) { return 'class="' + ($filtered -join ' ') + '"' }
            return ''
        })
        $attrs = $attrs -replace '\s{2,}', ' '
        $attrs = $attrs.Trim()
        if ($attrs.Length -gt 0) { return "<h$tag $attrs>" }
        return "<h$tag>"
    })
    if ($new -ne $text) { Set-Content -Path $file -Value $new -Encoding UTF8 }
}
