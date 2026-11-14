# Legacy Node collector entry point. Reuse the Python backend provider.
import contextlib
import io
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from etf_backend.provider import collect


def main():
    stage = "sdk_missing"
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import rqdatac
            request = json.loads(sys.argv[1])
            stage = "authentication"
            rqdatac.init(connect_timeout=8, timeout=40)
            stage = "query_failed"
            result = collect(rqdatac, request)
    except Exception:
        result = {"error": stage}
    print(json.dumps(result, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
