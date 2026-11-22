"""Incremental secret redaction and returned-output filtering across chunk edges."""
import re

TOKEN = re.compile(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+")
PREFIXES = ("github_pat_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_")


class DeltaRedactor:
    def __init__(self, secrets):
        self.secrets = tuple(secret for secret in secrets if secret)
        self.pending = ""

    def feed(self, text, *, final=False):
        self.pending += text
        # Redact complete known values before selecting a safe output boundary.
        for secret in self.secrets:
            self.pending = self.pending.replace(secret, "[REDACTED]")
        if final:
            output, self.pending = TOKEN.sub("[REDACTED]", self.pending), ""
            return output
        keep = 0
        for value in (*self.secrets, *PREFIXES):
            for size in range(min(len(value), len(self.pending)), 0, -1):
                if self.pending.endswith(value[:size]):
                    keep = max(keep, size)
                    break
        # A credential-shaped value can be longer than the configured token.
        # Hold it until its terminator, then redact the complete lexical value.
        for match in TOKEN.finditer(self.pending):
            if match.end() == len(self.pending):
                keep = max(keep, len(self.pending) - match.start())
        boundary = len(self.pending) - keep
        output, self.pending = self.pending[:boundary], self.pending[boundary:]
        return TOKEN.sub("[REDACTED]", output)


class OutputFilter:
    def __init__(self, maximum, stops):
        self.maximum, self.stops = maximum, stops
        self.pending, self.used, self.stopped, self.limited = "", 0, False, False

    def feed(self, text, *, final=False):
        if self.stopped or self.limited:
            return ""
        self.pending += text
        indices = [self.pending.find(stop) for stop in self.stops if stop in self.pending]
        if indices:
            self.pending = self.pending[:min(indices)]
            self.stopped, final = True, True
        keep = 0
        if not final:
            for stop in self.stops:
                for size in range(min(len(stop) - 1, len(self.pending)), 0, -1):
                    if self.pending.endswith(stop[:size]):
                        keep = max(keep, size)
                        break
        boundary = len(self.pending) - keep
        output, self.pending = self.pending[:boundary], self.pending[boundary:]
        encoded = output.encode("utf-8")
        remaining = self.maximum - self.used
        if len(encoded) > remaining:
            output = encoded[:remaining].decode("utf-8", errors="ignore")
            self.limited = True
            self.pending = ""
        self.used += len(output.encode("utf-8"))
        return output
