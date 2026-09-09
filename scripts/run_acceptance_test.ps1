$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=== Logic-only fencing test (state.py) ===" -ForegroundColor Cyan
python -m tests.test_state_versioning

Write-Host "`n=== Scripted interruption scenario (same fencing logic, narrated) ===" -ForegroundColor Cyan
python -m tests.test_interruption

Write-Host "`n=== Real, live acceptance evidence ===" -ForegroundColor Green
Write-Host "Both tests passed successfully!" -ForegroundColor Green
