#!/usr/bin/env python3
"""Export raw coding-agent sessions into `prompt history/`, redacting secrets only.

    python3 tool/export_sessions.py [--env-file F ...] [--extra DIR_OR_FILE ...] [--dry-run]

Sources (copied byte-for-byte except for secret redaction):
  - every Claude Code project dir under ~/.claude/projects whose name contains "ocean-above"
    (the main session, worktree workers, reviewers, their subagents);
  - --extra paths (e.g. session files fetched from the homelab deploy agent).
Redaction: literal values from the env files (default: every .env* in the repo except *.example) and well-known
token shapes. Each hit becomes `[REDACTED:<NAME>]`. Only counts are reported, never values.
The take-home requires raw, unedited sessions: do not change anything else.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'prompt history'
PROJECTS = Path.home() / '.claude' / 'projects'
PATTERNS = {
    'OPENAI_STYLE_KEY': r'sk-[A-Za-z0-9_\-]{20,}',
    'GITHUB_TOKEN': r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b',
    'AWS_KEY_ID': r'\bAKIA[0-9A-Z]{16}\b',
    'GOOGLE_API_KEY': r'\bAIza[0-9A-Za-z_\-]{35}\b',
    'SLACK_TOKEN': r'\bxox[abprs]-[A-Za-z0-9\-]{10,}\b',
    'PRIVATE_KEY': r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----',
}
MIN_LITERAL = 8  # shorter env values (ports, flags) are not secrets and would over-match


def env_secrets(files):
    out = {}
    for f in files:
        for line in Path(f).read_text(errors='ignore').splitlines():
            m = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$', line)
            if not m:
                continue
            name, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            if len(val) >= MIN_LITERAL and not val.startswith(('http://', 'https://')):
                out[val] = name
    return out


def redact(text, literals):
    counts = {}
    for val, name in sorted(literals.items(), key=lambda kv: -len(kv[0])):
        for form in {val, json.dumps(val)[1:-1]}:
            n = text.count(form)
            if n:
                text = text.replace(form, f'[REDACTED:{name}]')
                counts[name] = counts.get(name, 0) + n
    for name, pat in PATTERNS.items():
        text, n = re.subn(pat, f'[REDACTED:{name}]', text, flags=re.S)
        if n:
            counts[name] = counts.get(name, 0) + n
    return text, counts


def sources(extra):
    for d in sorted(PROJECTS.glob('*ocean-above*')):
        for f in sorted(d.rglob('*')):
            if f.is_file() and 'memory' not in f.relative_to(d).parts:  # memory notes are not sessions
                yield f, Path('claude-code') / d.name / f.relative_to(d)
    for e in extra:
        e = Path(e)
        files = [e] if e.is_file() else sorted(p for p in e.rglob('*') if p.is_file())
        for f in files:
            yield f, Path('extra') / e.name / (f.relative_to(e) if e.is_dir() else '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--env-file', action='append', default=None)
    ap.add_argument('--extra', action='append', default=[])
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    envs = a.env_file if a.env_file is not None else [
        p for p in ROOT.rglob('.env*')
        if p.is_file() and not p.name.endswith('.example') and 'node_modules' not in p.parts]
    literals = env_secrets(envs)
    report, total = [], 0
    for src, rel in sources(a.extra):
        text, counts = redact(src.read_text(errors='surrogateescape'), literals)
        dst = OUT / rel
        if counts:
            report.append(f'| `{rel}` | ' + ', '.join(f'{k}×{v}' for k, v in sorted(counts.items())) + ' |')
        total += 1
        if a.dry_run:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if counts:
            dst.write_text(text, errors='surrogateescape')
        else:
            shutil.copy2(src, dst)
    if not a.dry_run:
        (OUT / 'REDACTIONS.md').write_text(
            '# Redactions\n\nSecrets replaced by `[REDACTED:<NAME>]`; nothing else was edited.\n\n'
            '| File | Replacements |\n|---|---|\n' + '\n'.join(report) + '\n')
    print(f'{total} file(s) {"would be " if a.dry_run else ""}exported; {len(report)} with redactions; '
          f'{len(literals)} env value(s) from {len(envs)} env file(s) checked')


if __name__ == '__main__':
    sys.exit(main())
