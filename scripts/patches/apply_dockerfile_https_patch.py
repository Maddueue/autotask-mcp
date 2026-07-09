#!/usr/bin/env python3
"""
Dockerfile-Patch: Erzwingt HTTPS statt SSH fuer git-basierte npm-Dependencies
(autotask-node ist via github:-Shorthand referenziert). Notwendig, weil Azure
Container Registry Tasks in einer isolierten Umgebung ohne SSH-Keys baut.

Ausfuehren im Repo-Root (~/autotask-mcp):
    python3 apply_dockerfile_https_patch.py
"""
import sys

path = 'Dockerfile'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = """# Install dependencies (--ignore-scripts prevents 'prepare' from running before source is copied)
RUN npm ci --ignore-scripts"""

new = """# Force HTTPS instead of SSH for git-based npm dependencies (autotask-node is
# fetched via github: shorthand). ACR Tasks builds in an isolated environment
# with no SSH keys, so the default ssh://git@github.com/ rewrite some git
# configs apply would fail there.
RUN git config --global url."https://github.com/".insteadOf "ssh://git@github.com/"

# Install dependencies (--ignore-scripts prevents 'prepare' from running before source is copied)
RUN npm ci --ignore-scripts"""

count = content.count(old)
if count == 0:
    print(f"FEHLER: Erwarteter Original-Block nicht gefunden in {path}")
    sys.exit(1)
if count > 1:
    print(f"FEHLER: Block kommt {count}x vor (erwartet 1x), Abbruch zur Sicherheit")
    sys.exit(1)

content = content.replace(old, new)
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"OK: {path} gepatcht.")
