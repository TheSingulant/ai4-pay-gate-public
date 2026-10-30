import json
import subprocess
import sys

import pytest

from conftest import ROOT, make_order

from ai4_pay_gate.receipts import ReceiptLog, load_or_create_key, public_key_b64, verify_receipts


def _run_verify(*args):
    return subprocess.run([sys.executable, str(ROOT / "verify.py"), *map(str, args)], capture_output=True, text=True)


def test_key_file_mode(tmp_path):
    load_or_create_key(tmp_path / "k")
    assert oct((tmp_path / "k").stat().st_mode & 0o777) == "0o600"


def test_tamper_detected(gate):
    a = gate.approve(make_order(), "human")
    gate.pay(a["permit_id"], make_order())
    recs = json.loads(json.dumps(gate.receipts.all()))
    assert verify_receipts(recs, gate.receipts.pub)[0]
    recs[1]["body"]["total"] = "1.00"
    ok, errs = verify_receipts(recs, gate.receipts.pub)
    assert not ok and any("bad signature" in e for e in errs)


def test_deletion_and_reorder_detected(gate):
    for _ in range(3):
        gate.approve(make_order(), "human")
    recs = gate.receipts.all()
    assert not verify_receipts([recs[0], recs[2]], gate.receipts.pub)[0]
    assert not verify_receipts([recs[1], recs[0], recs[2]], gate.receipts.pub)[0]


def test_wrong_key_rejected(gate, tmp_path):
    gate.approve(make_order(), "human")
    other = public_key_b64(load_or_create_key(tmp_path / "other"))
    assert not verify_receipts(gate.receipts.all(), other)[0]


def test_verify_cli(gate, tmp_path):
    a = gate.approve(make_order(), "human")
    gate.pay(a["permit_id"], make_order())
    gate.pay(a["permit_id"], make_order())
    jl = tmp_path / "r.jsonl"
    r = _run_verify(jl, f"--pubkey={gate.receipts.pub}")
    assert r.returncode == 0 and "VALID" in r.stdout and "blocked=1" in r.stdout
    lines = jl.read_text().splitlines()
    rec = json.loads(lines[1]); rec["body"]["permit_id"] = "pmt_forged"; lines[1] = json.dumps(rec)
    jl.write_text("\n".join(lines) + "\n")
    r = _run_verify(jl, f"--pubkey={gate.receipts.pub}")
    assert r.returncode == 1 and "INVALID" in r.stdout


def test_receipt_log_reload_continues_chain(gate, tmp_path):
    from ai4_pay_gate.receipts import ReceiptLog
    gate.approve(make_order(), "human")
    key = load_or_create_key(tmp_path / "k")
    log2 = ReceiptLog(key, tmp_path / "r.jsonl")
    log2.append({"event": "test", "decision": "blocked"})
    assert verify_receipts(log2.all(), log2.pub)[0]


def test_verify_cli_pubkey_starting_with_dash(tmp_path):
    # base64url keys can begin with "-"; the documented --pubkey=KEY form must still parse
    for i in range(1000):
        k = load_or_create_key(tmp_path / f"k{i}")
        if public_key_b64(k).startswith("-"):
            break
    else:
        pytest.skip("no dash-prefixed key generated")
    ReceiptLog(k, tmp_path / "r.jsonl").append({"event": "approval", "decision": "approved", "reason": "ok"})
    r = _run_verify(tmp_path / "r.jsonl", f"--pubkey={public_key_b64(k)}")
    assert r.returncode == 0 and "VALID" in r.stdout, r.stderr
