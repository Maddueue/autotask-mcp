#!/usr/bin/env python3
"""
Impersonation-Patch fuer wyre-technology/autotask-mcp.
Fuegt ImpersonationResourceId-Support hinzu: Gateway-Header
X-Impersonation-Resource-Id -> ImpersonationResourceId-Header an Autotask.

Ausfuehren im Repo-Root (~/autotask-mcp):
    python3 apply_impersonation_patch.py

Das Skript ersetzt exakte Text-Bloecke (kein Zeilennummern-Diff), prueft
vor jeder Ersetzung, dass der Original-Block genau einmal vorkommt, und
bricht mit einer klaren Fehlermeldung ab, falls nicht - dann wurde der
Code seit unserer Analyse veraendert und wir muessen den Block neu ansehen.
"""
import sys

def apply_replacements(path, replacements):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    for old, new in replacements:
        count = content.count(old)
        if count == 0:
            print(f"FEHLER in {path}: Erwarteter Original-Block nicht gefunden:\n---\n{old}\n---")
            sys.exit(1)
        if count > 1:
            print(f"FEHLER in {path}: Block kommt {count}x vor (erwartet: 1x), Abbruch zur Sicherheit:\n---\n{old}\n---")
            sys.exit(1)
        content = content.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"OK: {path} gepatcht.")


# ---------------------------------------------------------------------------
# 1. src/types/mcp.ts - komplette Datei ist kurz, direkt neu schreiben
# ---------------------------------------------------------------------------
mcp_types_new = """// MCP Protocol Type Definitions
// Based on Model Context Protocol specification
export interface McpServerConfig {
  name: string;
  version: string;
  autotask: {
    username?: string;
    integrationCode?: string;
    secret?: string;
    apiUrl?: string;
    impersonationResourceId?: string;
  };
}
"""
with open('src/types/mcp.ts', 'w', encoding='utf-8') as f:
    f.write(mcp_types_new)
print("OK: src/types/mcp.ts gepatcht.")


# ---------------------------------------------------------------------------
# 2. src/utils/config.ts
# ---------------------------------------------------------------------------
config_ts_replacements = [
    (
"""export interface GatewayCredentials {
  username: string | undefined;
  secret: string | undefined;
  integrationCode: string | undefined;
  apiUrl: string | undefined;
}""",
"""export interface GatewayCredentials {
  username: string | undefined;
  secret: string | undefined;
  integrationCode: string | undefined;
  apiUrl: string | undefined;
  impersonationResourceId: string | undefined;
}"""
    ),
    (
"""export function getCredentialsFromGateway(): GatewayCredentials {
  return {
    username: process.env.X_API_KEY || process.env.AUTOTASK_USERNAME,
    secret: process.env.X_API_SECRET || process.env.AUTOTASK_SECRET,
    integrationCode: process.env.X_INTEGRATION_CODE || process.env.AUTOTASK_INTEGRATION_CODE,
    apiUrl: process.env.X_API_URL || process.env.AUTOTASK_API_URL,
  };
}""",
"""export function getCredentialsFromGateway(): GatewayCredentials {
  return {
    username: process.env.X_API_KEY || process.env.AUTOTASK_USERNAME,
    secret: process.env.X_API_SECRET || process.env.AUTOTASK_SECRET,
    integrationCode: process.env.X_INTEGRATION_CODE || process.env.AUTOTASK_INTEGRATION_CODE,
    apiUrl: process.env.X_API_URL || process.env.AUTOTASK_API_URL,
    impersonationResourceId: process.env.X_IMPERSONATION_RESOURCE_ID || process.env.AUTOTASK_IMPERSONATION_RESOURCE_ID,
  };
}"""
    ),
    (
"""  return {
    username: getHeader('x-api-key'),
    secret: getHeader('x-api-secret'),
    integrationCode: getHeader('x-integration-code'),
    apiUrl: getHeader('x-api-url'),
  };
}""",
"""  return {
    username: getHeader('x-api-key'),
    secret: getHeader('x-api-secret'),
    integrationCode: getHeader('x-integration-code'),
    apiUrl: getHeader('x-api-url'),
    impersonationResourceId: getHeader('x-impersonation-resource-id'),
  };
}"""
    ),
    (
"""  const creds = authMode === 'gateway'
    ? getCredentialsFromGateway()
    : {
        username: process.env.AUTOTASK_USERNAME,
        secret: process.env.AUTOTASK_SECRET,
        integrationCode: process.env.AUTOTASK_INTEGRATION_CODE,
        apiUrl: process.env.AUTOTASK_API_URL,
      };

  // Filter out undefined values to satisfy exactOptionalPropertyTypes
  const autotaskConfig: { username?: string; secret?: string; integrationCode?: string; apiUrl?: string } = {};
  if (creds.username) autotaskConfig.username = creds.username;
  if (creds.secret) autotaskConfig.secret = creds.secret;
  if (creds.integrationCode) autotaskConfig.integrationCode = creds.integrationCode;
  if (creds.apiUrl) autotaskConfig.apiUrl = creds.apiUrl;""",
"""  const creds = authMode === 'gateway'
    ? getCredentialsFromGateway()
    : {
        username: process.env.AUTOTASK_USERNAME,
        secret: process.env.AUTOTASK_SECRET,
        integrationCode: process.env.AUTOTASK_INTEGRATION_CODE,
        apiUrl: process.env.AUTOTASK_API_URL,
        impersonationResourceId: process.env.AUTOTASK_IMPERSONATION_RESOURCE_ID,
      };

  // Filter out undefined values to satisfy exactOptionalPropertyTypes
  const autotaskConfig: { username?: string; secret?: string; integrationCode?: string; apiUrl?: string; impersonationResourceId?: string } = {};
  if (creds.username) autotaskConfig.username = creds.username;
  if (creds.secret) autotaskConfig.secret = creds.secret;
  if (creds.integrationCode) autotaskConfig.integrationCode = creds.integrationCode;
  if (creds.apiUrl) autotaskConfig.apiUrl = creds.apiUrl;
  if (creds.impersonationResourceId) autotaskConfig.impersonationResourceId = creds.impersonationResourceId;"""
    ),
    (
"""    autotask: {
      username: mcpArgs?.autotask?.username || envConfig.autotask.username,
      secret: mcpArgs?.autotask?.secret || envConfig.autotask.secret,
      integrationCode: mcpArgs?.autotask?.integrationCode || envConfig.autotask.integrationCode,
      apiUrl: mcpArgs?.autotask?.apiUrl || envConfig.autotask.apiUrl
    }""",
"""    autotask: {
      username: mcpArgs?.autotask?.username || envConfig.autotask.username,
      secret: mcpArgs?.autotask?.secret || envConfig.autotask.secret,
      integrationCode: mcpArgs?.autotask?.integrationCode || envConfig.autotask.integrationCode,
      apiUrl: mcpArgs?.autotask?.apiUrl || envConfig.autotask.apiUrl,
      impersonationResourceId: mcpArgs?.autotask?.impersonationResourceId || envConfig.autotask.impersonationResourceId
    }"""
    ),
]
apply_replacements('src/utils/config.ts', config_ts_replacements)


# ---------------------------------------------------------------------------
# 3. src/mcp/server.ts
# ---------------------------------------------------------------------------
server_ts_replacements = [
    (
"""    const autotaskConfig: McpServerConfig['autotask'] = {};
    if (credentials.username) autotaskConfig.username = credentials.username;
    if (credentials.secret) autotaskConfig.secret = credentials.secret;
    if (credentials.integrationCode) autotaskConfig.integrationCode = credentials.integrationCode;
    if (credentials.apiUrl) autotaskConfig.apiUrl = credentials.apiUrl;""",
"""    const autotaskConfig: McpServerConfig['autotask'] = {};
    if (credentials.username) autotaskConfig.username = credentials.username;
    if (credentials.secret) autotaskConfig.secret = credentials.secret;
    if (credentials.integrationCode) autotaskConfig.integrationCode = credentials.integrationCode;
    if (credentials.apiUrl) autotaskConfig.apiUrl = credentials.apiUrl;
    if (credentials.impersonationResourceId) autotaskConfig.impersonationResourceId = credentials.impersonationResourceId;"""
    ),
    (
"""        'Content-Type, Accept, Authorization, Mcp-Session-Id, X-API-Key, X-API-Secret, X-Integration-Code'""",
"""        'Content-Type, Accept, Authorization, Mcp-Session-Id, X-API-Key, X-API-Secret, X-Integration-Code, X-Impersonation-Resource-Id'"""
    ),
]
apply_replacements('src/mcp/server.ts', server_ts_replacements)


# ---------------------------------------------------------------------------
# 4. src/services/autotask.service.ts
# ---------------------------------------------------------------------------
service_ts_replacements = [
    (
"""      const { username, secret, integrationCode, apiUrl } = this.config.autotask;
      if (!username || !secret || !integrationCode) {
        throw new Error('Missing required Autotask credentials: username, secret, and integrationCode are required');
      }
      this.logger.info('Initializing Autotask HTTP client...');
      this.http = new AutotaskHttpClient(username, secret, integrationCode, apiUrl, this.logger);""",
"""      const { username, secret, integrationCode, apiUrl, impersonationResourceId } = this.config.autotask;
      if (!username || !secret || !integrationCode) {
        throw new Error('Missing required Autotask credentials: username, secret, and integrationCode are required');
      }
      this.logger.info('Initializing Autotask HTTP client...');
      this.http = new AutotaskHttpClient(username, secret, integrationCode, apiUrl, this.logger, impersonationResourceId);"""
    ),
]
apply_replacements('src/services/autotask.service.ts', service_ts_replacements)


# ---------------------------------------------------------------------------
# 5. src/services/autotask-http.ts
# ---------------------------------------------------------------------------
http_ts_replacements = [
    (
"""export class AutotaskHttpClient {
  private resolvedBaseUrl: string | null = null;
  constructor(
    private readonly username: string,
    private readonly secret: string,
    private readonly integrationCode: string,
    private readonly apiUrl: string | undefined,
    private readonly logger: Logger
  ) {}""",
"""export class AutotaskHttpClient {
  private resolvedBaseUrl: string | null = null;
  constructor(
    private readonly username: string,
    private readonly secret: string,
    private readonly integrationCode: string,
    private readonly apiUrl: string | undefined,
    private readonly logger: Logger,
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

print("\nFertig. Alle 5 Dateien gepatcht:")
print("  - src/types/mcp.ts")
print("  - src/utils/config.ts")
print("  - src/mcp/server.ts")
print("  - src/services/autotask.service.ts")
print("  - src/services/autotask-http.ts")
print("\nNaechster Schritt: npm run build && npm test")
