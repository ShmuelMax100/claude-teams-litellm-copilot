"""One-shot JSON Schema checks in a killable, credential-free process."""
import json
import math
import os
from pathlib import Path
import sys


def main():
    import resource
    from jsonschema import Draft202012Validator
    # Budget additional CPU/allocation after trusted interpreter imports. The
    # parent's wall deadline also bounds startup. Emulated AMD64 interpreters
    # reserve a large virtual address range, so an absolute AS cap kills them
    # before validation even begins; measuring that baseline keeps a real cap.
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu_limit = math.ceil(usage.ru_utime + usage.ru_stime) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    if sys.platform == "linux":
        baseline = int(Path("/proc/self/statm").read_text().split()[0]) * os.sysconf("SC_PAGE_SIZE")
        allocation_limit = baseline + 256 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (allocation_limit, allocation_limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        payload = json.load(sys.stdin)
        for schema in payload["schemas"].values():
            Draft202012Validator.check_schema(schema)
        for call in payload.get("calls", []):
            Draft202012Validator(payload["schemas"][call["name"]]).validate(call["arguments"])
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
