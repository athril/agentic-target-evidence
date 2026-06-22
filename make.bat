@echo off
setlocal EnableDelayedExpansion
rem ====================================================================
rem  make.bat - Windows wrapper that mirrors the Unix Makefile targets.
rem
rem  Usage:  make <target> [args]
rem  Example: make up
rem           make run BRCA1 "breast cancer"
rem           make test
rem
rem  Requires: Docker Desktop (docker compose) and uv on PATH.
rem ====================================================================

set "DC=docker compose"
set "PYTEST=uv run pytest"

rem -- Service groups (keep in sync with the Makefile) -----------------
set "INFRA_SERVICES=postgres redis clickhouse"
set "LANGFUSE_SERVICES=minio minio-setup langfuse-web langfuse-worker"
set "OTEL_SERVICES=otel-collector"
set "APP_SERVICES=ollama data-acquisition agents-knowledge agents-reasoning report planner mcp-gateway chat"

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=help"

set "VALID= dirs up down restart ps logs infra langfuse otel stop-app stop-otel stop-langfuse stop-infra logs-langfuse logs-otel logs-infra install mcp-serve mcp-chat chat run test test-smoke test-schemas db-migrate clean-volumes help "
echo %VALID% | findstr /i /c:" %TARGET% " >nul
if errorlevel 1 (
    echo.
    echo Unknown target: %TARGET%
    echo.
    call :help
    exit /b 1
)

rem -- Dispatch -------------------------------------------------------
call :%TARGET% %2 %3 %4 %5 %6 %7 %8 %9
exit /b %errorlevel%

rem ===================================================================
rem  Top-level targets
rem ===================================================================

:dirs
rem Create host-side result directories before Docker claims them as root
for %%D in (results\original results\data results\report) do (
    if not exist "%%D" mkdir "%%D"
)
exit /b 0

:up
rem Start everything (infra + Langfuse + OTEL + app)
call :dirs
%DC% up -d %INFRA_SERVICES% %LANGFUSE_SERVICES% %OTEL_SERVICES% %APP_SERVICES%
exit /b %errorlevel%

:down
rem Stop and remove all containers (data volumes preserved)
%DC% down
exit /b %errorlevel%

:restart
rem Full stop + start
call :down
call :up
exit /b %errorlevel%

:ps
rem Show running container status
%DC% ps
exit /b %errorlevel%

:logs
rem Tail logs for all running services (Ctrl-C to exit)
%DC% logs -f
exit /b %errorlevel%

rem ===================================================================
rem  Selective start targets
rem ===================================================================

:infra
rem Start only infrastructure (postgres, redis, clickhouse)
%DC% up -d %INFRA_SERVICES%
exit /b %errorlevel%

:langfuse
rem Start Langfuse stack (requires infra)
call :infra
%DC% up -d %LANGFUSE_SERVICES%
echo Langfuse UI -^> http://localhost:3000  (admin@gtv.local / admin)
exit /b %errorlevel%

:otel
rem Start OTEL collector (requires Langfuse)
call :langfuse
%DC% up -d %OTEL_SERVICES%
exit /b %errorlevel%

rem ===================================================================
rem  Selective stop targets
rem ===================================================================

:stop-app
rem Stop only application containers
%DC% stop %APP_SERVICES%
exit /b %errorlevel%

:stop-otel
rem Stop OTEL collector
%DC% stop %OTEL_SERVICES%
exit /b %errorlevel%

:stop-langfuse
rem Stop Langfuse services
%DC% stop %LANGFUSE_SERVICES%
exit /b %errorlevel%

:stop-infra
rem Stop infrastructure (postgres, redis, clickhouse)
%DC% stop %INFRA_SERVICES%
exit /b %errorlevel%

rem ===================================================================
rem  Log tailing helpers
rem ===================================================================

:logs-langfuse
rem Tail Langfuse web + worker logs
%DC% logs -f langfuse-web langfuse-worker
exit /b %errorlevel%

:logs-otel
rem Tail OTEL collector logs
%DC% logs -f otel-collector
exit /b %errorlevel%

:logs-infra
rem Tail postgres + redis + clickhouse logs
%DC% logs -f %INFRA_SERVICES%
exit /b %errorlevel%

rem ===================================================================
rem  Development
rem ===================================================================

:install
rem Install Python dependencies
uv sync
exit /b %errorlevel%

:mcp-serve
rem Run the MCP gateway (all public connectors as one server)
uv run atv-mcp
exit /b %errorlevel%

:mcp-chat
rem Run the chat assistant locally (Gradio + Ollama + MCP tools)
uv run --group chat atv-chat
exit /b %errorlevel%

:chat
rem Start the chat assistant + its MCP gateway as Docker services
%DC% up -d mcp-gateway chat
exit /b %errorlevel%

:run
rem Run analysis:  make run GENE "DISEASE" [TISSUE] [POPULATION]
rem Defaults match the Makefile (PTPN1 / pancreatic cancer).
set "GENE=%~1"
set "DISEASE=%~2"
set "TISSUE=%~3"
set "POPULATION=%~4"
if "%GENE%"=="" set "GENE=PTPN1"
if "%DISEASE%"=="" set "DISEASE=pancreatic cancer"
set "EXTRA="
if not "%TISSUE%"=="" set "EXTRA=!EXTRA! --tissue "%TISSUE%""
if not "%POPULATION%"=="" set "EXTRA=!EXTRA! --population "%POPULATION%""
uv run python src/run_analysis.py "%GENE%" "%DISEASE%"!EXTRA!
exit /b %errorlevel%

:test
rem Run all tests (excluding smoke)
%PYTEST% tests/ -m "not smoke" -q
exit /b %errorlevel%

:test-smoke
rem Run end-to-end smoke test (requires Ollama + internet)
%PYTEST% tests/smoke/ -v -s -m smoke
exit /b %errorlevel%

:test-schemas
rem Run schema / contract tests only
%PYTEST% tests/schemas/ -v
exit /b %errorlevel%

rem ===================================================================
rem  Data management
rem ===================================================================

:db-migrate
rem Run Alembic migrations against the running postgres.
rem Load .env into the environment, then upgrade.
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        set "line=%%A"
        if not "!line!"=="" if not "!line:~0,1!"=="#" set "%%A=%%B"
    )
)
uv run alembic upgrade head
exit /b %errorlevel%

:clean-volumes
rem Remove ALL data volumes (destructive - asks for confirmation)
echo This will delete postgres-data, clickhouse-data, and redis-data.
set "CONFIRM="
set /p "CONFIRM=Type YES to confirm: "
if /i not "!CONFIRM!"=="YES" (
    echo Aborted.
    exit /b 1
)
%DC% down -v
exit /b %errorlevel%

rem ===================================================================
rem  Help
rem ===================================================================

:help
echo.
echo Usage: make ^<target^>
echo.
echo Targets:
echo   dirs            Create host-side result directories
echo   up              Start everything (infra + Langfuse + OTEL + app)
echo   down            Stop and remove all containers (volumes preserved)
echo   restart         Full stop + start
echo   ps              Show running container status
echo   logs            Tail logs for all running services
echo   infra           Start only infrastructure (postgres, redis, clickhouse)
echo   langfuse        Start Langfuse stack (requires infra)
echo   otel            Start OTEL collector (requires Langfuse)
echo   stop-app        Stop only application containers
echo   stop-otel       Stop OTEL collector
echo   stop-langfuse   Stop Langfuse services
echo   stop-infra      Stop infrastructure
echo   logs-langfuse   Tail Langfuse web + worker logs
echo   logs-otel       Tail OTEL collector logs
echo   logs-infra      Tail postgres + redis + clickhouse logs
echo   install         Install Python dependencies (uv sync)
echo   mcp-serve       Run the MCP gateway
echo   mcp-chat        Run the chat assistant locally
echo   chat            Start chat + MCP gateway as Docker services
echo   run             Run analysis: make run BRCA1 "breast cancer"
echo   test            Run all tests (excluding smoke)
echo   test-smoke      Run end-to-end smoke test
echo   test-schemas    Run schema / contract tests only
echo   db-migrate      Run Alembic migrations against running postgres
echo   clean-volumes   Remove ALL data volumes (destructive)
echo   help            Show this help
echo.
exit /b 0
