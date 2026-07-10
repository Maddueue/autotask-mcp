#!/usr/bin/env python3
"""
Patch: Shared-secret header check for gateway mode (defense in depth).

Adds an optional GATEWAY_SHARED_SECRET env var. When set, every request to
POST /mcp must carry a matching X-Gateway-Secret header (constant-time
compare) before anything else runs -- independent of, and prior to, the
existing per-request gateway credential check (X-API-Key etc.).

Intended usage: APIM injects X-Gateway-Secret from a Key Vault-backed named
value. A request that reaches the Container App directly, bypassing APIM,
without the header is rejected here.

Run from the repo root:
    python3 scripts/patches/004_gateway_shared_secret.py

Idempotent: safe to run multiple times, and safe to run after the file has
already been patched (each edit checks first).

Tested against the current main branch of Maddueue/autotask-mcp
(src/utils/config.ts, src/mcp/server.ts) on 10.07.2026 -- all 7 replacements
applied cleanly, script is idempotent on a second run. Not run through the
project's own test suite / tsc (no repo access in this session) -- run
`npm test` after applying, before committing.
"""

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

CONFIG_TS = REPO_ROOT / "src" / "utils" / "config.ts"
SERVER_TS = REPO_ROOT / "src" / "mcp" / "server.ts"


def replace_once(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")

    if new in text:
        print(f"[skip] {label}: already applied")
        return

    if old not in text:
        print(f"[FAIL] {label}: expected original text not found in {path}")
        print("       File may have changed since this patch was written -- inspect manually.")
        sys.exit(1)

    count = text.count(old)
    if count != 1:
        print(f"[FAIL] {label}: expected exactly 1 match, found {count} in {path}")
        sys.exit(1)

    path.write_text(text.replace(old, new), encoding="utf-8")
    print(f"[ok]   {label}")


def patch_config_ts() -> None:
    # 1. Add gatewaySharedSecret to the auth block of EnvironmentConfig
    replace_once(
        CONFIG_TS,
        old="""  auth: {
    mode: AuthMode;
  };
  lazyLoading?: boolean;
}""",
        new="""  auth: {
    mode: AuthMode;
    gatewaySharedSecret?: string;
  };
  lazyLoading?: boolean;
}""",
        label="config.ts: EnvironmentConfig.auth.gatewaySharedSecret field",
    )

    # 2. Populate it in loadEnvironmentConfig()'s return value
    replace_once(
        CONFIG_TS,
        old="""    auth: {
      mode: authMode
    },
    lazyLoading: process.env.LAZY_LOADING === 'true' || process.env.LAZY_LOADING === '1'""",
        new="""    auth: {
      mode: authMode,
      ...(process.env.GATEWAY_SHARED_SECRET ? { gatewaySharedSecret: process.env.GATEWAY_SHARED_SECRET } : {})
    },
    lazyLoading: process.env.LAZY_LOADING === 'true' || process.env.LAZY_LOADING === '1'""",
        label="config.ts: loadEnvironmentConfig() reads GATEWAY_SHARED_SECRET",
    )

    # 3. Document the new env var in getConfigHelp()
    replace_once(
        CONFIG_TS,
        old="""  X_INTEGRATION_CODE       - Autotask integration code (from X-Integration-Code header)

=== Common Options ===""",
        new="""  X_INTEGRATION_CODE       - Autotask integration code (from X-Integration-Code header)
  GATEWAY_SHARED_SECRET     - Optional. If set, every POST /mcp request must carry a matching
                              X-Gateway-Secret header (constant-time compare) before anything
                              else runs. Intended to be injected by APIM from a Key Vault-backed
                              named value -- rejects requests that bypass APIM and hit the
                              Container App directly.

=== Common Options ===""",
        label="config.ts: getConfigHelp() documents GATEWAY_SHARED_SECRET",
    )


def patch_server_ts() -> None:
    # 1. Import crypto primitives for the constant-time compare
    replace_once(
        SERVER_TS,
        old="""// Main MCP Server Implementation
// Handles the Model Context Protocol server setup and integration with Autotask
// Supports both local (env-based) and gateway (header-based) credential modes

import { createServer, IncomingMessage, ServerResponse, Server as HttpServer } from 'node:http';""",
        new="""// Main MCP Server Implementation
// Handles the Model Context Protocol server setup and integration with Autotask
// Supports both local (env-based) and gateway (header-based) credential modes

import { createServer, IncomingMessage, ServerResponse, Server as HttpServer } from 'node:http';
import { createHash, timingSafeEqual } from 'node:crypto';""",
        label="server.ts: import createHash/timingSafeEqual",
    )

    # 2. Add the constant-time comparison helper, right before the class
    replace_once(
        SERVER_TS,
        old="""import { registerPromptHandlers } from './prompts.js';

export class AutotaskMcpServer {""",
        new="""import { registerPromptHandlers } from './prompts.js';

/**
 * Constant-time comparison of a request-supplied X-Gateway-Secret header
 * against the configured shared secret. Both sides are SHA-256 hashed first
 * so that (a) differing lengths never short-circuit the comparison and leak
 * timing information, and (b) a missing/undefined header can't throw from
 * timingSafeEqual's length check.
 */
function isValidGatewaySecret(headerValue: string | string[] | undefined, expected: string): boolean {
  const provided = Array.isArray(headerValue) ? headerValue[0] : headerValue;
  const providedHash = createHash('sha256').update(provided ?? '').digest();
  const expectedHash = createHash('sha256').update(expected).digest();
  return timingSafeEqual(providedHash, expectedHash);
}

export class AutotaskMcpServer {""",
        label="server.ts: isValidGatewaySecret() helper",
    )

    # 3. Read the configured secret alongside isGatewayMode
    replace_once(
        SERVER_TS,
        old="""    const isGatewayMode = this.envConfig?.auth?.mode === 'gateway';

    this.httpServer = createServer""",
        new="""    const isGatewayMode = this.envConfig?.auth?.mode === 'gateway';
    const gatewaySharedSecret = this.envConfig?.auth?.gatewaySharedSecret;

    this.httpServer = createServer""",
        label="server.ts: read gatewaySharedSecret from config",
    )

    # 4. Enforce the header check first thing inside the /mcp handler
    replace_once(
        SERVER_TS,
        old="""      // MCP endpoint — stateless: fresh server + transport per request
      if (url.pathname === '/mcp') {
        // Only POST is supported in stateless mode
        if (req.method !== 'POST') {""",
        new="""      // MCP endpoint — stateless: fresh server + transport per request
      if (url.pathname === '/mcp') {
        // Defense-in-depth: if a shared secret is configured, require it on
        // every /mcp request before anything else runs. APIM injects this
        // header from a Key Vault-backed named value, so a request that
        // reaches the Container App directly (bypassing APIM) without the
        // header is rejected here -- independent of, and prior to, the
        // per-request gateway credential check further down.
        if (gatewaySharedSecret && !isValidGatewaySecret(req.headers['x-gateway-secret'], gatewaySharedSecret)) {
          res.writeHead(401, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({
            jsonrpc: '2.0',
            error: { code: -32001, message: 'Unauthorized: invalid or missing gateway secret' },
            id: null,
          }));
          return;
        }

        // Only POST is supported in stateless mode
        if (req.method !== 'POST') {""",
        label="server.ts: enforce X-Gateway-Secret check in /mcp handler",
    )


def main() -> None:
    for path in (CONFIG_TS, SERVER_TS):
        if not path.exists():
            print(f"[FAIL] {path} not found -- run this script from the repo root.")
            sys.exit(1)

    patch_config_ts()
    patch_server_ts()
    print("\nDone. Review with `git diff`, then run the test suite before committing.")


if __name__ == "__main__":
    main()
