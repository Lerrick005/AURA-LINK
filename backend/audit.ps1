$ErrorActionPreference = "Continue"
$Out = "audit_output.txt"
if (Test-Path $Out) { Remove-Item $Out }
function Section($t){ "`n`n===== $t =====`n" | Out-File $Out -Append -Encoding utf8 }
function Cmd($l,$b){ "`n--- $l ---`n" | Out-File $Out -Append -Encoding utf8; try { & $b 2>&1 | Out-File $Out -Append -Encoding utf8 } catch { "ERROR: $_" | Out-File $Out -Append -Encoding utf8 } }

Section "0. CONTEXT";            Cmd "pwd" { Get-Location }; Cmd "python" { python --version }
Section "1. GIT FILES";          Cmd "git ls-files" { git ls-files }
Section "2. GIT STATUS";         Cmd "status" { git status --short }
Section "3. ALEMBIC";            Cmd "current" { alembic current }; Cmd "history" { alembic history }
Section "4. TESTS RUN";          Cmd "pytest" { pytest -q }
Section "5. TENANT TEST";        Cmd "file" { Get-Content tests\db\test_tenant_isolation.py -Raw }
Section "6. DB CONFTEST";        Cmd "file" { Get-Content tests\db\conftest.py -Raw }
Section "7. MIG 0001";           Cmd "file" { Get-Content migrations\versions\0001_initial.py -Raw }
Section "8. ENGINE";             Cmd "file" { Get-Content app\modules\runtime\engine.py -Raw }
Section "9. DECISION";           Cmd "file" { Get-Content app\modules\runtime\decision.py -Raw }
Section "10. REGISTRY";          Cmd "file" { Get-Content app\modules\runtime\registry.py -Raw }
Section "11. DEPS";              Cmd "file" { Get-Content app\api\deps.py -Raw }
Section "12. CONFIG";            Cmd "file" { Get-Content app\core\config.py -Raw }
Section "13. DB";                Cmd "file" { Get-Content app\core\db.py -Raw }
Section "14. MAIN";              Cmd "file" { Get-Content app\main.py -Raw }
Section "15. AUTH GREP";         Cmd "grep" { Get-ChildItem -Recurse -Include *.py | Where-Object { $_.FullName -notmatch '\\\.venv\\|__pycache__' } | Select-String -Pattern 'def authorize|def require_|class .*Policy|current_user|get_current_user|Role|permission' | Select-Object -First 40 }
Section "16. AUDIT GREP";        Cmd "grep" { Select-String -Path migrations\versions\*.py -Pattern 'audit' -Context 1,1 }
Section "17. FRONTEND";          Cmd "check" { $r = Split-Path (Get-Location).Path -Parent; if (Test-Path "$r\frontend") { Get-ChildItem "$r\frontend" | Select-Object Name } else { "NO frontend at $r" } }
Section "18. RUNCTX CAP";        Cmd "grep" { Select-String -Path app\modules\runtime\*.py -Pattern 'max_messages|truncat|len\(messages' -Context 1,1 }
Section "19. TTL";               Cmd "grep" { Get-ChildItem -Recurse -Include *.py | Where-Object { $_.FullName -notmatch '\\\.venv\\|__pycache__' } | Select-String -Pattern 'expires_at|cleanup|ttl' | Select-Object -First 20 }
Section "20. SMOKE";             Cmd "file" { Get-Content smoke_engine.py -Raw }
Section "21. AGENT MODELS";      Cmd "file" { Get-Content app\modules\agents\models.py -Raw }
Section "22. REQS";              Cmd "file" { Get-Content requirements.txt -Raw }
"DONE" | Out-File $Out -Append -Encoding utf8
Write-Host "OK -> $((Get-Item $Out).Length) bytes"
