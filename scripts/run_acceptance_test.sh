#!/usr/bin/env bash
set -e

echo "=== Logic-only fencing test (state.py) ==="
python -m tests.test_state_versioning

echo ""
echo "=== Scripted interruption scenario (same fencing logic, narrated) ==="
python -m tests.test_interruption

echo ""
echo "=== Real, live acceptance evidence ==="
echo "The tests above verify the SAME fencing code the live agent uses,"
echo "but without a real voice pipeline. The strongest evidence is a"
echo "real interruption captured live — run this manually and save the log:"
echo ""
echo "  python -m agent.main console 2>&1 | tee logs/console_session_N.txt"
echo "  (Windows: set \$PSDefaultParameterValues['Out-File:Encoding']='utf8' first,"
echo "   then pipe to 'Out-File -Encoding utf8' instead of tee — PowerShell"
echo "   defaults to UTF-16, which most tools can't read.)"
echo ""
echo "Speak a stock request, then interrupt with a different one within"
echo "~3 seconds while the tool call is in flight. See RIME_EVIDENCE.md"
echo "for two real captured examples and how to read them."