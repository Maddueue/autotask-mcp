#!/usr/bin/env python3
"""
Impersonation-Patch Teil 2 - nur fuer die 2 Dateien, die beim ersten Lauf
fehlgeschlagen sind (autotask.service.ts, autotask-http.ts).
Nutzt kuerzere Ein-/Zwei-Zeilen-Anker statt ganzer Bloecke, damit
Leerzeilen zwischen Anweisungen keine Rolle mehr spielen.

Ausfuehren im Repo-Root (~/autotask-mcp):
    python3 apply_impersonation_patch_part2.py

WICHTIG: Nur ausfuehren, wenn Teil 1 bereits gelaufen ist und die Meldung
"FEHLER in src/services/autotask.service.ts" kam - die anderen 3 Dateien
werden hier nicht noch einmal beruehrt.
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


# ---------------------------------------------------------------------------
# src/services/autotask.service.ts - zwei einzelne Zeilen statt Block
# ---------------------------------------------------------------------------
service_ts_replacements = [
    (
        "const { username, secret, integrationCode, apiUrl } = this.config.autotask;",
        "const { username, secret, integrationCode, apiUrl, impersonationResourceId } = this.config.autotask;"
    ),
    (
        "this.http = new AutotaskHttpClient(username, secret, integrationCode, apiUrl, this.logger);",
        "this.http = new AutotaskHttpClient(username, secret, integrationCode, apiUrl, this.logger, impersonationResourceId);"
    ),
]
apply_replacements('src/services/autotask.service.ts', service_ts_replacements)


# ---------------------------------------------------------------------------
# src/services/autotask-http.ts - Konstruktor (kurzer Anker) + headers()
# ---------------------------------------------------------------------------
http_ts_replacements = [
    (
"""    private readonly logger: Logger
  ) {}""",
"""    private readonly logger: Logger,
    private readonly impersonationResourceId?: string
  ) {}"""
    ),
    (
"""  private headers(): Record<string, string> {
    return {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ApiIntegrationcode: this.integrationCode,
      UserName: this.username,
      Secret: this.secret,
    };
  }""",
"""  private headers(): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ApiIntegrationcode: this.integrationCode,
      UserName: this.username,
      Secret: this.secret,
    };
    if (this.impersonationResourceId) {
      headers.ImpersonationResourceId = this.impersonationResourceId;
    }
    return headers;
  }"""
    ),
]
apply_replacements('src/services/autotask-http.ts', http_ts_replacements)

print("\nFertig. Beide verbleibenden Dateien gepatcht.")
print("Naechster Schritt: npm run build && npm test")
