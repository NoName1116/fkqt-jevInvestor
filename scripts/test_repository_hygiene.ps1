$requiredTracked = @('.gitignore', 'README.md')
foreach ($required in $requiredTracked) {
    git ls-files --error-unmatch $required 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "缺少必须跟踪的文件: $required"
    }
}

$forbiddenTracked = git ls-files | Where-Object {
    $_ -match '(^|/)(\.env|\.venv|data|cache)(/|$)' -or
    $_ -match '\.(db|sqlite|sqlite3)$'
}
if ($forbiddenTracked) {
    throw "禁止跟踪的文件: $($forbiddenTracked -join ', ')"
}

$secretMatches = git grep -n -I -E 'apikey_[0-9a-f]{16,}|TYPESAFE_API_KEY=[A-Za-z0-9_-]{16,}' -- . ':!scripts/test_repository_hygiene.ps1'
if ($LASTEXITCODE -eq 0) {
    throw "检测到疑似 Secret: $secretMatches"
}
if ($LASTEXITCODE -ne 1) {
    throw "git grep 执行失败，退出码 $LASTEXITCODE"
}
