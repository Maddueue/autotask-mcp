#!/usr/bin/env python3
"""
Korrektur des vorherigen Dockerfile-Patches: node:22-alpine hat kein git
vorinstalliert. 'git config' schlaegt daher mit 'git: not found' fehl.
Installiert git zuerst per apk, dann erst die Konfiguration.

Ausfuehren im Repo-Root (~/autotask-mcp):
    python3 apply_dockerfile_git_fix.py
"""
import sys

path = 'Dockerfile'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = """RUN git config --global url."https://github.com/".insteadOf "ssh://git@github.com/\""""

new = """RUN apk add --no-cache git && \\
    git config --global url."https://github.com/".insteadOf "ssh://git@github.com/\""""

count = content.count(old)
if count == 0:
    print(f"FEHLER: Erwarteter Original-Text nicht gefunden in {path}")
    sys.exit(1)
if count > 1:
    print(f"FEHLER: Text kommt {count}x vor (erwartet 1x), Abbruch zur Sicherheit")
    sys.exit(1)

content = content.replace(old, new)
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"OK: {path} gepatcht (git-Installation ergaenzt).")
