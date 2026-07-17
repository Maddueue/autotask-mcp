#!/usr/bin/env python3
"""
Patch: Phase 7 -- auto-resolve write-impersonation from the authenticated
employee's Entra identity (X-User-Principal header, injected by APIM from
the validated token's "upn" claim).

Design (verified against the real tenant on 13.07.2026 -- see
Fortschritts_Log.md entry for 13.07.2026):
  - The local part (before "@") of an employee's Entra UPN matches Autotask's
    Resources.userName field exactly, case-insensitively -- for every one of
    23 real employee accounts checked, including 3 whose Autotask `email`
    field uses a different domain (@cristie.partners) than the company's
    normal login convention. userName was therefore chosen as the match
    target instead of `email`/`email2`/`email3`.
  - Matching is an EXACT comparison (not "starts with"), so that suffixed
    service/API accounts sharing an employee's email (e.g.
    "Mathias.Hinkelmann_API") never match instead of the real account.
  - If X-Impersonation-Resource-Id is already explicitly set by the caller,
    it is NOT overridden -- manual override still works (useful for testing).
  - If no X-User-Principal header is present, or no cache match is found,
    the request proceeds WITHOUT impersonation (fails open) rather than
    being rejected. This is a deliberate choice, not an oversight -- flagged
    to Mathias for confirmation, not yet independently re-confirmed in this
    patch.

Adds:
  - src/utils/config.ts:
      - resourceIdCache (Map<lowercased userName, Autotask Resource ID>),
        refreshed at most every 15 minutes via a direct REST call to
        Resources/query (bypasses the autotask-node SDK, same rationale as
        the existing resolveAutotaskApiUrl: this can run ahead of full
        AutotaskService construction).
      - resolveImpersonationResourceIdFromPrincipal(): resolves a UPN/email
        to a Resource ID via the cache, refreshing it first if stale.
      - getUserPrincipalFromHeaders(): reads the X-User-Principal header.
      - _resetResourceIdCache(): test-only reset hook.
  - src/mcp/server.ts:
      - Extracts a new dispatchMcpRequest() private method (fresh
        server+transport creation, previously inline) so both the
        synchronous (env mode) and asynchronous (gateway mode, after
        awaiting the impersonation lookup) code paths share it.
      - In gateway mode: if no explicit X-Impersonation-Resource-Id and an
        X-User-Principal header is present, resolves it before building
        per-request handlers. Lookup failures are logged and swallowed
        (fail open), never block the request.
      - Adds X-User-Principal to the CORS Access-Control-Allow-Headers list.

Does NOT touch:
  - APIM policy (needs a separate `az rest` PUT -- see accompanying command
    delivered alongside this patch, not in this script).
  - Entra app registration optional claims (needs a separate Graph PATCH to
    add "upn" as an optional access-token claim -- also delivered alongside
    this patch, not in this script).

Tested on 13.07.2026: applied cleanly and idempotently against a sandbox
reconstruction of the real src/utils/config.ts and src/mcp/server.ts (both
fetched verbatim from Maddueue/autotask-mcp main branch); brace/paren/
bracket balance verified in both patched files. tsc/npm test could not be
run in this sandbox (no npm registry access) -- run both after applying,
before committing.

Run from the repo root:
    python3 scripts/patches/005_phase7_impersonation_resolution.py

Idempotent: safe to run multiple times, and safe to run after the file has
already been patched (each edit checks first).
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
    # 1. Insert the resource-ID cache + resolver functions right after
    #    resolveAutotaskApiUrl(), before getConfigHelp().
    replace_once(
        CONFIG_TS,
        old="""  zoneUrlCache.set(cacheKey, url);
  return url;
}

/**
 * Get configuration help text
 */
export function getConfigHelp(): string {""",
        new="""  zoneUrlCache.set(cacheKey, url);
  return url;
}

/**
 * In-memory cache mapping an Autotask Resource's `userName` field
 * (lowercased) to its numeric Resource ID. Populated by
 * resolveImpersonationResourceIdFromPrincipal on a (rate-limited) full
 * Resources query. Never persisted to disk -- lifetime == process lifetime.
 *
 * Phase 7: lets the gateway auto-resolve write-impersonation from the
 * authenticated employee's Entra identity (UPN or email -- either works,
 * since we only ever compare the local part before "@") without requiring
 * every employee's own Autotask API credentials to be provisioned.
 *
 * Verified against the real tenant on 13.07.2026: for every real employee
 * account, the local part of the primary Autotask `email` field matches
 * `userName` exactly (case-insensitively) -- including employees whose
 * `email` domain differs from the company's Autotask login convention
 * (e.g. "name@cristie.partners" with userName "Name.Surname"). `email2`/
 * `email3` must NOT be used for this comparison -- they may hold unrelated
 * personal addresses. Matching is deliberately an *exact* comparison (not
 * "starts with") so that suffixed service/API accounts sharing an
 * employee's email (e.g. "Name.Surname_API") never match instead of the
 * real account.
 */
const resourceIdCache = new Map<string, number>();
let resourceIdCacheFetchedAt = 0;
const RESOURCE_ID_CACHE_TTL_MS = 15 * 60 * 1000; // 15 minutes

/**
 * Reset the resource ID cache. Intended for tests only.
 */
export function _resetResourceIdCache(): void {
  resourceIdCache.clear();
  resourceIdCacheFetchedAt = 0;
}

/**
 * Minimal Autotask API credential triple needed to query the Resources
 * entity directly (bypasses the autotask-node SDK, same rationale as
 * resolveAutotaskApiUrl: this can run ahead of full service construction).
 */
export interface ResourceLookupCredentials {
  username: string;
  secret: string;
  integrationCode: string;
}

/**
 * Refresh the resource ID cache from the Autotask REST API, unless it was
 * refreshed within RESOURCE_ID_CACHE_TTL_MS. Fetches only active resources,
 * since inactive/system accounts should never be impersonation targets.
 */
async function refreshResourceIdCacheIfStale(
  apiUrl: string,
  creds: ResourceLookupCredentials,
  logger: ZoneResolverLogger,
  fetchImpl: typeof fetch
): Promise<void> {
  const now = Date.now();
  if (resourceIdCache.size > 0 && now - resourceIdCacheFetchedAt < RESOURCE_ID_CACHE_TTL_MS) {
    return;
  }

  const response = await fetchImpl(`${apiUrl}/v1.0/Resources/query`, {
    method: 'POST',
    headers: {
      ApiIntegrationCode: creds.integrationCode,
      UserName: creds.username,
      Secret: creds.secret,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      filter: [{ op: 'eq', field: 'isActive', value: true }],
      includeFields: ['id', 'userName'],
    }),
  });

  if (!response.ok) {
    throw new Error(`Resources query for impersonation cache failed: HTTP ${response.status}`);
  }

  const body: any = await response.json();
  const items: any[] = Array.isArray(body?.items) ? body.items : [];

  resourceIdCache.clear();
  for (const item of items) {
    if (typeof item?.userName === 'string' && item.userName.length > 0 && typeof item?.id === 'number') {
      resourceIdCache.set(item.userName.toLowerCase(), item.id);
    }
  }
  resourceIdCacheFetchedAt = now;
  logger.info(`Refreshed impersonation resource-ID cache: ${resourceIdCache.size} active resources.`);
}

/**
 * Resolve an Autotask Resource ID for write-impersonation from an
 * authenticated employee's Entra identity claim (UPN or email -- both work,
 * see cache doc comment above for why). Returns undefined (not a throw) if
 * no match is found, so the caller can fail open and proceed without
 * impersonation rather than rejecting the request outright.
 */
export async function resolveImpersonationResourceIdFromPrincipal(
  userPrincipal: string,
  apiUrl: string,
  creds: ResourceLookupCredentials,
  logger: ZoneResolverLogger,
  fetchImpl: typeof fetch = fetch
): Promise<number | undefined> {
  const localPart = userPrincipal.split('@')[0]?.toLowerCase();
  if (!localPart) {
    return undefined;
  }

  await refreshResourceIdCacheIfStale(apiUrl, creds, logger, fetchImpl);
  return resourceIdCache.get(localPart);
}

/**
 * Extract the caller's identity claim from request headers. APIM forwards
 * this as X-User-Principal, populated from the validated Entra token's
 * "upn" claim (added as an optional access-token claim on the app
 * registration). Used only to look up a Phase 7 impersonation target --
 * never used as a credential itself.
 */
export function getUserPrincipalFromHeaders(headers: Record<string, string | string[] | undefined>): string | undefined {
  const value = headers['x-user-principal'] || headers['X-User-Principal'];
  return Array.isArray(value) ? value[0] : value;
}

/**
 * Get configuration help text
 */
export function getConfigHelp(): string {""",
        label="config.ts: resourceIdCache + resolveImpersonationResourceIdFromPrincipal()",
    )

    # 2. Document the new header in getConfigHelp()
    replace_once(
        CONFIG_TS,
        old="""  GATEWAY_SHARED_SECRET     - Optional. If set, every POST /mcp request must carry a matching
                              X-Gateway-Secret header (constant-time compare) before anything
                              else runs. Intended to be injected by APIM from a Key Vault-backed
                              named value -- rejects requests that bypass APIM and hit the
                              Container App directly.

=== Common Options ===""",
        new="""  GATEWAY_SHARED_SECRET     - Optional. If set, every POST /mcp request must carry a matching
                              X-Gateway-Secret header (constant-time compare) before anything
                              else runs. Intended to be injected by APIM from a Key Vault-backed
                              named value -- rejects requests that bypass APIM and hit the
                              Container App directly.
  X-User-Principal (header) - Optional, not an env var. If present and X-Impersonation-Resource-Id
                              is not explicitly set, the server resolves this header's local part
                              (before "@") against Autotask's Resources.userName field
                              (case-insensitive exact match) and uses the match, if any, as the
                              impersonation target for this request. Intended to be injected by
                              APIM from the validated Entra token's "upn" claim. No match -> the
                              request proceeds without impersonation (fails open).

=== Common Options ===""",
        label="config.ts: getConfigHelp() documents X-User-Principal header",
    )


def patch_server_ts() -> None:
    # 1. Import the new config.ts helpers
    replace_once(
        SERVER_TS,
        old="""import { EnvironmentConfig, parseCredentialsFromHeaders, GatewayCredentials, getServerVersion } from '../utils/config.js';""",
        new="""import {
  EnvironmentConfig,
  parseCredentialsFromHeaders,
  GatewayCredentials,
  getServerVersion,
  resolveAutotaskApiUrl,
  resolveImpersonationResourceIdFromPrincipal,
  getUserPrincipalFromHeaders,
} from '../utils/config.js';""",
        label="server.ts: import Phase 7 config.ts helpers",
    )

    # 2. Add X-User-Principal to the CORS allow-headers list
    replace_once(
        SERVER_TS,
        old="""        'Content-Type, Accept, Authorization, Mcp-Session-Id, X-API-Key, X-API-Secret, X-Integration-Code, X-Impersonation-Resource-Id'""",
        new="""        'Content-Type, Accept, Authorization, Mcp-Session-Id, X-API-Key, X-API-Secret, X-Integration-Code, X-Impersonation-Resource-Id, X-User-Principal'""",
        label="server.ts: CORS allow-headers includes X-User-Principal",
    )

    # 3. Extract dispatchMcpRequest() as its own method, right after
    #    buildPerRequestHandlers().
    replace_once(
        SERVER_TS,
        old="""    const service = new AutotaskService(requestConfig, this.logger);
    return {
      resourceHandler: new AutotaskResourceHandler(service, this.logger),
      toolHandler: new AutotaskToolHandler(service, this.logger, this.lazyLoading),
    };
  }

  /**
   * Build a fresh MCP `Server` for a single request, optionally bound to""",
        new="""    const service = new AutotaskService(requestConfig, this.logger);
    return {
      resourceHandler: new AutotaskResourceHandler(service, this.logger),
      toolHandler: new AutotaskToolHandler(service, this.logger, this.lazyLoading),
    };
  }

  /**
   * Create a fresh MCP server + Streamable HTTP transport for a single
   * request and hand it the request/response pair. Extracted so both the
   * synchronous (env mode) and asynchronous (gateway mode, after awaiting
   * the Phase 7 impersonation lookup) code paths in startHttpTransport
   * share the exact same dispatch logic.
   */
  private dispatchMcpRequest(
    req: IncomingMessage,
    res: ServerResponse,
    perRequestToolHandler?: AutotaskToolHandler,
    perRequestResourceHandler?: AutotaskResourceHandler,
  ): void {
    const server = this.createFreshServer(perRequestToolHandler, perRequestResourceHandler);
    const transport = new StreamableHTTPServerTransport({
      enableJsonResponse: true,
    });

    res.on('close', () => {
      transport.close();
      server.close();
    });

    server.connect(transport as unknown as Transport).then(() => {
      transport.handleRequest(req, res);
    }).catch((err) => {
      this.logger.error('MCP transport error:', err);
      if (!res.headersSent) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          jsonrpc: '2.0',
          error: { code: -32603, message: 'Internal error' },
          id: null,
        }));
      }
    });
  }

  /**
   * Build a fresh MCP `Server` for a single request, optionally bound to""",
        label="server.ts: extract dispatchMcpRequest() method",
    )

    # 4. Rewire the /mcp gateway branch to resolve impersonation before
    #    dispatching, and route the non-gateway branch through the same
    #    dispatchMcpRequest() method.
    replace_once(
        SERVER_TS,
        old="""        // In gateway mode, build per-request service + handlers from the
        // injected credential headers. Each request gets its own isolated
        // AutotaskService so concurrent requests for different tenants
        // never interfere with each other.
        let perRequestToolHandler: AutotaskToolHandler | undefined;
        let perRequestResourceHandler: AutotaskResourceHandler | undefined;
        if (isGatewayMode) {
          const credentials = parseCredentialsFromHeaders(req.headers as Record<string, string | string[] | undefined>);
          if (credentials.username && credentials.secret && credentials.integrationCode) {
            const handlers = this.buildPerRequestHandlers(credentials);
            perRequestToolHandler = handlers.toolHandler;
            perRequestResourceHandler = handlers.resourceHandler;
          } else {
            // Gateway mode REQUIRES per-request credentials. Falling through
            // to the env-configured `this.toolHandler` would serve the server
            // operator's tenant data to whoever sent the unauthenticated
            // request — a cross-tenant leak. Reject explicitly instead.
            res.writeHead(401, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
              jsonrpc: '2.0',
              error: {
                code: -32001,
                message: 'Unauthorized: missing required gateway credentials (X-API-Key, X-API-Secret, X-Integration-Code)',
              },
              id: null,
            }));
            return;
          }
        }

        // Stateless: create fresh server + transport for each request
        const server = this.createFreshServer(perRequestToolHandler, perRequestResourceHandler);
        const transport = new StreamableHTTPServerTransport({
          enableJsonResponse: true,
        });

        res.on('close', () => {
          transport.close();
          server.close();
        });

        server.connect(transport as unknown as Transport).then(() => {
          transport.handleRequest(req, res);
        }).catch((err) => {
          this.logger.error('MCP transport error:', err);
          if (!res.headersSent) {
            res.writeHead(500, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
              jsonrpc: '2.0',
              error: { code: -32603, message: 'Internal error' },
              id: null,
            }));
          }
        });

        return;
      }""",
        new="""        // In gateway mode, build per-request service + handlers from the
        // injected credential headers. Each request gets its own isolated
        // AutotaskService so concurrent requests for different tenants
        // never interfere with each other.
        if (isGatewayMode) {
          const credentials = parseCredentialsFromHeaders(req.headers as Record<string, string | string[] | undefined>);
          if (credentials.username && credentials.secret && credentials.integrationCode) {
            // Phase 7: if the caller didn't already provide an explicit
            // X-Impersonation-Resource-Id override, try to auto-resolve one
            // from the authenticated employee's identity (forwarded by APIM
            // as X-User-Principal from the validated Entra token's "upn"
            // claim). No match, or any lookup failure, fails open --
            // request proceeds without impersonation rather than rejecting.
            const userPrincipal = credentials.impersonationResourceId
              ? undefined
              : getUserPrincipalFromHeaders(req.headers as Record<string, string | string[] | undefined>);

            const impersonationLookup: Promise<void> = userPrincipal
              ? resolveAutotaskApiUrl(credentials.username, credentials.apiUrl, this.logger)
                  .then((apiUrl) =>
                    resolveImpersonationResourceIdFromPrincipal(
                      userPrincipal,
                      apiUrl,
                      {
                        username: credentials.username as string,
                        secret: credentials.secret as string,
                        integrationCode: credentials.integrationCode as string,
                      },
                      this.logger
                    )
                  )
                  .then((resolvedId) => {
                    if (resolvedId) {
                      credentials.impersonationResourceId = String(resolvedId);
                      this.logger.info(`Resolved impersonation resource ID ${resolvedId} for principal "${userPrincipal}".`);
                    } else {
                      this.logger.warn(`No Autotask resource matched principal "${userPrincipal}" -- proceeding without impersonation.`);
                    }
                  })
                  .catch((err) => {
                    this.logger.error('Impersonation resource-ID lookup failed -- proceeding without impersonation:', err);
                  })
              : Promise.resolve();

            impersonationLookup.then(() => {
              const handlers = this.buildPerRequestHandlers(credentials);
              this.dispatchMcpRequest(req, res, handlers.toolHandler, handlers.resourceHandler);
            });
          } else {
            // Gateway mode REQUIRES per-request credentials. Falling through
            // to the env-configured `this.toolHandler` would serve the server
            // operator's tenant data to whoever sent the unauthenticated
            // request — a cross-tenant leak. Reject explicitly instead.
            res.writeHead(401, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
              jsonrpc: '2.0',
              error: {
                code: -32001,
                message: 'Unauthorized: missing required gateway credentials (X-API-Key, X-API-Secret, X-Integration-Code)',
              },
              id: null,
            }));
          }
          return;
        }

        // Non-gateway (env) mode: no per-request credentials, use the
        // default env-configured handlers.
        this.dispatchMcpRequest(req, res);
        return;
      }""",
        label="server.ts: gateway branch resolves impersonation before dispatch",
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
