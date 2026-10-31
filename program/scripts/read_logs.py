from pathlib import Path
for p in [Path("data/logs/oaagent-8077.err.log"), Path("data/logs/oaagent-8077.log")]:
    if p.exists():
        raw = p.read_bytes()
        print(f"=== {p.name} ({len(raw)} bytes) ===")
        for enc in ("utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                print(f"Decoded with {enc} (total lines: {len(lines)}):")
                for line in lines[-25:]:
                    print("  ", line)
                break
            except Exception:
                continue
