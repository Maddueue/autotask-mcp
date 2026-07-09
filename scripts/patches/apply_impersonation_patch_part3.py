#!/usr/bin/env python3
"""
Impersonation-Patch Teil 3 - Nachtrag: EnvironmentConfig-Interface fehlte.
Behebt: TS2339 Property 'impersonationResourceId' does not exist on type
'{ username?: string; secret?: string; integrationCode?: string; apiUrl?: string; }'

Ausfuehren im Repo-Root (~/autotask-mcp):
    python3 apply_impersonation_patch_part3.py
"""
import sys

def apply_replacements(path, replacements):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    for old, new in replacements:
        count = content.count(old)
        if count == 0:
            print(f"FEHLER in {path}: Erwarteter Original-Text nicht gefunden:\n---\n{old}\n---")
            sys.exit(1)
        if count > 1:
            print(f"FEHLER in {path}: Text kommt {count}x vor (erwartet: 1x), Abbruch zur Sicherheit:\n---\n{old}\n---")
            sys.exit(1)
        content = content.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"OK: {path} gepatcht.")


config_ts_replacements = [
    (
"""export interface EnvironmentConfig {
  autotask: {
    username?: string;
    secret?: string;
    integrationCode?: string;
    apiUrl?: string;
  };""",
"""export interface EnvironmentConfig {
  autotask: {
    username?: string;
    secret?: string;
    integrationCode?: string;
    apiUrl?: string;
    impersonationResourceId?: string;
  };"""
    ),
]
apply_replacements('src/utils/config.ts', config_ts_replacements)

print("\nFertig. Naechster Schritt: npm run build && npm test")
