# Copyright © 2026 XbhbxZty
#
# 一键启动前后端（中间件由 start-services.sh / docker compose 负责，本脚本不碰）
#
#   .\start-app.ps1            体检 + 启动前后端
#   .\start-app.ps1 check      只体检，不启动
#   .\start-app.ps1 stop       停掉本脚本起的前后端
#
# ## 为什么重点是体检而不是"少敲两条命令"
#
# 实测两次浪费都不是因为命令难敲，而是因为**依赖悄悄挂了却没人告诉你**：
#
#   1. Milvus 与 PostgreSQL 中途掉线 → 尽调跑完才发现 29 次检索全失败、零证据
#   2. 代理客户端停止 → DNS 仍解析到 198.18.0.x 假 IP，所有阿里云域名进黑洞。
#      文档上传报"文档提交失败"，而真实原因（网络不可达）被吞掉了
#
# 两次都是跑完/失败之后才回头查。所以启动前一次性把四件事验掉：
# 关系库、向量库、模型端点可达性、密钥是否配置。任何一项不过，
# 明确告诉你是哪一项、以及怎么修——而不是让你在半小时后从日志里挖。

param([string]$Command = "start")

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Ok = 0; $Warn = 0; $Fail = 0

function Say($icon, $color, $msg) { Write-Host "  $icon $msg" -ForegroundColor $color }
function Pass($msg) { $script:Ok++;   Say "OK  " Green  $msg }
function Warn($msg) { $script:Warn++; Say "WARN" Yellow $msg }
function Fail($msg) { $script:Fail++; Say "FAIL" Red    $msg }

# ---------------------------------------------------------------- 体检

function Test-Tcp($targetHost, $port, $timeoutMs = 6000) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($targetHost, $port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($timeoutMs)) { return $false }
        $client.EndConnect($async); return $true
    } catch { return $false } finally { $client.Close() }
}

function Read-DotEnv($path) {
    $map = @{}
    if (-not (Test-Path $path)) { return $map }
    foreach ($line in Get-Content $path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $map[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $map
}

function Invoke-Preflight {
    Write-Host "`n[1/4] 依赖文件" -ForegroundColor Cyan
    if (Test-Path "$Root\backend\.env") { Pass "backend/.env" } else { Fail "backend/.env 缺失（LLM 与数据库配置都在这里）" }
    if (Test-Path "$Root\frontend\.env") { Pass "frontend/.env" } else { Fail "frontend/.env 缺失" }
    if (Test-Path "$Root\frontend\node_modules") { Pass "frontend/node_modules" }
    else { Fail "frontend/node_modules 缺失 —— 先跑 cd frontend; npm install" }

    $env_ = Read-DotEnv "$Root\backend\.env"

    Write-Host "`n[2/4] 中间件（Docker 起的那些，本脚本只检查不启动）" -ForegroundColor Cyan
    $pgHost = if ($env_.POSTGRES_HOST) { $env_.POSTGRES_HOST } else { "localhost" }
    $pgPort = if ($env_.POSTGRES_PORT) { [int]$env_.POSTGRES_PORT } else { 5432 }
    if (Test-Tcp $pgHost $pgPort) { Pass "PostgreSQL ${pgHost}:${pgPort}" }
    else { Fail "PostgreSQL ${pgHost}:${pgPort} 不可达 —— 人机协同的暂停/恢复依赖它；先起 Docker" }

    $mvHost = if ($env_.MILVUS_HOST) { $env_.MILVUS_HOST } else { "localhost" }
    $mvPort = if ($env_.MILVUS_PORT) { [int]$env_.MILVUS_PORT } else { 29530 }
    if (Test-Tcp $mvHost $mvPort) { Pass "Milvus ${mvHost}:${mvPort}" }
    else { Fail "Milvus ${mvHost}:${mvPort} 不可达 —— 本地知识库检索会整条失效（且表现为『没搜到』而非报错）" }

    # 模型端点。这一项是本脚本存在的主要理由：它挂了的时候，
    # 系统各处报出来的都是含糊的下游错误（"文档提交失败"之类），
    # 唯独不会告诉你真实原因是网络不通。
    Write-Host "`n[3/4] 模型与文档服务端点" -ForegroundColor Cyan
    $llmBase = if ($env_.LLM_BASE_URL) { $env_.LLM_BASE_URL } else { "https://dashscope.aliyuncs.com/compatible-mode/v1" }
    $llmHost = ([System.Uri]$llmBase).Host
    foreach ($h in @($llmHost, "openplatform.aliyuncs.com")) {
        if (Test-Tcp $h 443) { Pass "$h : 443" }
        else {
            $ip = try { [System.Net.Dns]::GetHostAddresses($h)[0].IPAddressToString } catch { "解析失败" }
            if ($ip -like "198.18.*") {
                Fail "$h 不可达（解析到 $ip）—— 这是代理 fake-IP 段，通常意味着**代理客户端已停止**但 DNS 仍指向它。重启代理后再试，必要时 ipconfig /flushdns"
            } else {
                Fail "$h 不可达（解析到 $ip）—— 模型调用与文档解析都会失败"
            }
        }
    }

    Write-Host "`n[4/4] 密钥" -ForegroundColor Cyan
    foreach ($k in @("DASHSCOPE_API_KEY", "DOCMIND_ACCESS_KEY_ID", "DOCMIND_ACCESS_KEY_SECRET")) {
        if ($env_[$k]) { Pass "$k 已配置（长度 $($env_[$k].Length)）" }
        else { Warn "$k 未配置" }
    }
    if (-not $env_.DASHSCOPE_API_KEY) { Warn "没有 DASHSCOPE_API_KEY，需要 LLM 的完整研究流程跑不了；纯规则回归 (eval/run_fast.py) 仍可用" }

    Write-Host "`n体检结果: 通过 $Ok / 警告 $Warn / 失败 $Fail" -ForegroundColor $(if ($Fail) { "Red" } else { "Green" })
    return $Fail -eq 0
}

# ---------------------------------------------------------------- 启停

function Start-App {
    # 日志必须落盘。上一版用 -WindowStyle Minimized 起进程且不重定向：
    # 子进程一崩，stderr 连同那个最小化窗口一起消失，脚本只会干等到超时，
    # 然后报一句“未就绪”——真实原因一个字都看不到。这正是本仓库一路在修的
    # 那个毛病（把失败伪装成含糊的状态），不该由启动脚本再犯一次。
    $logDir = Join-Path $Root "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $beOut = Join-Path $logDir "backend.log"
    $beErr = Join-Path $logDir "backend.err.log"
    $feOut = Join-Path $logDir "frontend.log"
    $feErr = Join-Path $logDir "frontend.err.log"

    Write-Host "`n升级数据库 schema 到 Alembic head..." -ForegroundColor Cyan
    Push-Location "$Root\backend"
    try {
        & python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "Alembic upgrade head 失败（ExitCode=$LASTEXITCODE）；后端未启动。"
        }
    } finally {
        Pop-Location
    }

    Write-Host "`n启动后端 (localhost:8000)..." -ForegroundColor Cyan
    $backend = Start-Process -PassThru -WindowStyle Hidden -FilePath "python" `
        -ArgumentList "-X", "utf8", "app/app_main.py" -WorkingDirectory "$Root\backend" `
        -RedirectStandardOutput $beOut -RedirectStandardError $beErr
    Write-Host "  PID $($backend.Id)   日志 logs/backend.err.log"

    Write-Host "启动前端 (localhost:5183)..." -ForegroundColor Cyan
    $frontend = Start-Process -PassThru -WindowStyle Hidden -FilePath "cmd.exe" `
        -ArgumentList "/c", "npm run dev" -WorkingDirectory "$Root\frontend" `
        -RedirectStandardOutput $feOut -RedirectStandardError $feErr
    Write-Host "  PID $($frontend.Id)   日志 logs/frontend.log"

    "$($backend.Id)`n$($frontend.Id)" | Set-Content "$Root\.app-pids" -Encoding utf8

    # 就绪检测用裸 TCP，不用 Invoke-WebRequest：后者走系统代理，
    # 实测同一个 localhost:8000 —— 裸 TCP 9ms，Invoke-WebRequest 2061ms。
    # 拿一个受代理影响的探针去判断本机端口，本身就是错的工具。
    Write-Host "`n等待后端就绪（最多 40 秒）..." -ForegroundColor Cyan
    $ready = $false
    foreach ($i in 1..20) {
        if ($backend.HasExited) {
            Fail "后端进程已退出，ExitCode=$($backend.ExitCode)"
            Write-Host "`n--- logs/backend.err.log 末尾 ---" -ForegroundColor Red
            Get-Content $beErr -Tail 20 -ErrorAction SilentlyContinue
            return
        }
        if (Test-Tcp "127.0.0.1" 8000 1500) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }

    if ($ready) { Pass "后端已就绪" }
    else {
        Warn "后端 40 秒内未监听 8000（进程仍在运行）"
        Write-Host "`n--- logs/backend.err.log 末尾 ---" -ForegroundColor Yellow
        Get-Content $beErr -Tail 20 -ErrorAction SilentlyContinue
    }

    if (Test-Tcp "127.0.0.1" 5183 1500) { Pass "前端已就绪" }
    else { Warn "前端尚未监听 5183，通常再等几秒；异常见 logs/frontend.log" }

    Write-Host ""
    Write-Host "  前端      http://localhost:5183" -ForegroundColor Green
    Write-Host "  后端 API  http://localhost:8000"
    Write-Host "  接口文档  http://localhost:8000/docs"
    Write-Host "  日志      logs\backend.err.log / logs\frontend.log"
    Write-Host "`n  停止:  .\start-app.ps1 stop`n"
}

function Stop-App {
    if (-not (Test-Path "$Root\.app-pids")) { Write-Host "没有找到本脚本启动的进程记录"; return }
    foreach ($pidText in Get-Content "$Root\.app-pids") {
        if (-not $pidText.Trim()) { continue }
        try {
            Stop-Process -Id ([int]$pidText) -Force -ErrorAction Stop
            Write-Host "  已停止 PID $pidText"
        } catch { Write-Host "  PID $pidText 已不在运行" }
    }
    Remove-Item "$Root\.app-pids" -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------- 主逻辑

switch ($Command) {
    "check" { [void](Invoke-Preflight) }
    "stop"  { Stop-App }
    "start" {
        $healthy = Invoke-Preflight
        if (-not $healthy) {
            Write-Host "`n体检未通过。修掉上面标 FAIL 的项再启动，" -ForegroundColor Red
            Write-Host "否则问题会在半小时后以别的形式出现（跑完发现零证据、或一句含糊的『处理失败』）。" -ForegroundColor Red
            Write-Host "确实要强行启动就跑: .\start-app.ps1 force`n" -ForegroundColor Yellow
            exit 1
        }
        Start-App
    }
    "force" { Write-Host "跳过体检强行启动" -ForegroundColor Yellow; Start-App }
    default {
        Write-Host @"
用法: .\start-app.ps1 [命令]

  start   体检 + 启动前后端（默认）
  check   只体检，不启动
  stop    停掉本脚本起的前后端
  force   跳过体检直接启动

中间件（PostgreSQL / Milvus / Redis / ES）由 docker compose 负责，本脚本不碰：
  docker compose up -d
"@
    }
}
