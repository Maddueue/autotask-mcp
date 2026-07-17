// Configuration Utility
// Handles loading configuration from environment variables and MCP client arguments
// Supports gateway mode where credentials come via HTTP headers

import { McpServerConfig } from '../types/mcp.js';
import { LogLevel } from './logger.js';
// `resolveJsonModule` is enabled in tsconfig. We read package.json so the
// runtime can report its actual built version in the MCP initialize handshake
// and the /health endpoint. The Dockerfile patches this file's `version` field
// with the release VERSION build arg before `npm run build`, so the published
// image always carries the real release version even though branch protection
// blocks @semantic-release/git from pushing version bumps back to main.
import packageJson from '../../package.json';

/**
 * Resolve the running server version. Single source of truth for both the
 * `serverInfo.version` MCP handshake value and the `/health` response.
 * Priority: explicit override > package.json (patched at Docker build time
 * with the release version) > 'unknown'.
 */
export function getServerVersion(override?: string): string {
  return override || packageJson.version || 'unknown';
}

export type TransportType = 'stdio' | 'http';
export type AuthMode = 'env' | 'gateway';

export interface EnvironmentConfig {
  autotask: {
    username?: string;
    secret?: string;
    integrationCode?: string;
    apiUrl?: string;
    impersonationResourceId?: string;
  };
  server: {
    name: string;
    version: string;
  };
  transport: {
    type: TransportType;
    port: number;
    host: string;
  };
  logging: {
    level: LogLevel;
    format: 'json' | 'simple';
  };
  auth: {
    mode: AuthMode;
    gatewaySharedSecret?: string;
  };
  lazyLoading?: boolean;
}

/**
 * Gateway credentials extracted from HTTP request headers
 * The MCP Gateway injects credentials via these headers:
 * - X-API-Key: Contains the Autotask username
 * - X-API-Secret: Contains the Autotask secret
 * - X-Integration-Code: Contains the Autotask integration code
 */
export interface GatewayCredentials {
  username: string | undefined;
  secret: string | undefined;
  integrationCode: string | undefined;
  apiUrl: string | undefined;
  impersonationResourceId: string | undefined;
}

/**
 * Extract credentials from gateway-injected environment variables
 * The gateway proxies headers as environment variables:
 * - X-API-Key header -> X_API_KEY env var
 * - X-API-Secret header -> X_API_SECRET env var
 * - X-Integration-Code header -> X_INTEGRATION_CODE env var
 */
export function getCredentialsFromGateway(): GatewayCredentials {
  return {
    username: process.env.X_API_KEY || process.env.AUTOTASK_USERNAME,
    secret: process.env.X_API_SECRET || process.env.AUTOTASK_SECRET,
    integrationCode: process.env.X_INTEGRATION_CODE || process.env.AUTOTASK_INTEGRATION_CODE,
    apiUrl: process.env.X_API_URL || process.env.AUTOTASK_API_URL,
    impersonationResourceId: process.env.X_IMPERSONATION_RESOURCE_ID || process.env.AUTOTASK_IMPERSONATION_RESOURCE_ID,
  };
}

/**
 * Parse credentials from HTTP request headers (for per-request credential handling)
 * Header names follow HTTP convention (lowercase with hyphens)
 */
export function parseCredentialsFromHeaders(headers: Record<string, string | string[] | undefined>): GatewayCredentials {
  const getHeader = (name: string): string | undefined => {
    const value = headers[name] || headers[name.toLowerCase()];
    return Array.isArray(value) ? value[0] : value;
  };

  return {
    username: getHeader('x-api-key'),
    secret: getHeader('x-api-secret'),
    integrationCode: getHeader('x-integration-code'),
    apiUrl: getHeader('x-api-url'),
    impersonationResourceId: getHeader('x-impersonation-resource-id'),
  };
}

/**
 * Load configuration from environment variables
 */
export function loadEnvironmentConfig(): EnvironmentConfig {
  // Support both direct env vars and gateway-injected vars
  // Gateway vars (X_API_KEY, etc.) take precedence when in gateway mode
  const authMode = (process.env.AUTH_MODE as AuthMode) || 'env';

  // getCredentialsFromGateway falls back to AUTOTASK_* env vars internally,
  // so it works for both modes. In env mode, use AUTOTASK_* vars directly.
  const creds = authMode === 'gateway'
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
  if (creds.impersonationResourceId) autotaskConfig.impersonationResourceId = creds.impersonationResourceId;

  const transportType = (process.env.MCP_TRANSPORT as TransportType) || 'stdio';
  if (transportType !== 'stdio' && transportType !== 'http') {
    throw new Error(`Invalid MCP_TRANSPORT value: "${transportType}". Must be "stdio" or "http".`);
  }

  return {
    autotask: autotaskConfig,
    server: {
      name: process.env.MCP_SERVER_NAME || 'autotask-mcp',
      version: getServerVersion(process.env.MCP_SERVER_VERSION)
    },
    transport: {
      type: transportType,
      port: parseInt(process.env.MCP_HTTP_PORT || '8080', 10),
      host: process.env.MCP_HTTP_HOST || '0.0.0.0'
    },
    logging: {
      level: (process.env.LOG_LEVEL as LogLevel) || 'info',
      format: (process.env.LOG_FORMAT as 'json' | 'simple') || 'simple'
    },
    auth: {
      mode: authMode,
      ...(process.env.GATEWAY_SHARED_SECRET ? { gatewaySharedSecret: process.env.GATEWAY_SHARED_SECRET } : {})
    },
    lazyLoading: process.env.LAZY_LOADING === 'true' || process.env.LAZY_LOADING === '1'
  };
}

/**
 * Merge environment config with MCP client configuration
 */
export function mergeWithMcpConfig(envConfig: EnvironmentConfig, mcpArgs?: Record<string, any>): McpServerConfig {
  // MCP client can override server configuration through arguments
  const serverConfig: McpServerConfig = {
    name: mcpArgs?.name || envConfig.server.name,
    version: mcpArgs?.version || envConfig.server.version,
    autotask: {
      username: mcpArgs?.autotask?.username || envConfig.autotask.username,
      secret: mcpArgs?.autotask?.secret || envConfig.autotask.secret,
      integrationCode: mcpArgs?.autotask?.integrationCode || envConfig.autotask.integrationCode,
      apiUrl: mcpArgs?.autotask?.apiUrl || envConfig.autotask.apiUrl,
      impersonationResourceId: mcpArgs?.autotask?.impersonationResourceId || envConfig.autotask.impersonationResourceId
    }
  };

  return serverConfig;
}

/**
 * In-memory cache of resolved zone URLs keyed by username (lowercased).
 * Populated by resolveAutotaskApiUrl on successful zone info lookup.
 * Never persisted to disk — lifetime == process lifetime.
 */
const zoneUrlCache = new Map<string, string>();

/**
 * Reset the zone URL cache. Intended for tests only.
 */
export function _resetZoneUrlCache(): void {
  zoneUrlCache.clear();
}

/**
 * Minimal logger shape accepted by resolveAutotaskApiUrl so we don't
 * have a hard dep on the Logger class (keeps this pre-auth bootstrap simple).
 */
export interface ZoneResolverLogger {
  info: (msg: string, ...args: any[]) => void;
  error: (msg: string, ...args: any[]) => void;
}

const ZONE_INFO_URL = 'https://webservices.autotask.net/atservicesrest/v1.0/zoneInformation';
const ZONE_DOCS_URL =
  'https://ww1.autotask.net/help/Content/AdminSetup/2ExtensionsIntegrations/APIs/REST/General_Topics/REST_Zones.htm';

/**
 * Resolve the Autotask API base URL.
 *
 * Precedence:
 *   1. If `explicitApiUrl` is set, return it (manual override always wins).
 *   2. Otherwise, if `username` is set, look up the tenant's zone via the
 *      unauthenticated zoneInformation endpoint, cache it, and return it.
 *   3. Otherwise, throw — caller must set AUTOTASK_API_URL manually.
 *
 * Intentionally uses native `fetch` (not the autotask-node SDK) because
 * this is a pre-auth bootstrap: the SDK needs a URL to construct itself.
 */
export async function resolveAutotaskApiUrl(
  username: string | undefined,
  explicitApiUrl: string | undefined,
  logger: ZoneResolverLogger,
  fetchImpl: typeof fetch = fetch
): Promise<string> {
  if (explicitApiUrl) {
    return explicitApiUrl;
  }

  if (!username) {
    throw new Error(
      'Cannot auto-detect Autotask zone: AUTOTASK_USERNAME is not set. ' +
        `Set AUTOTASK_API_URL manually — see ${ZONE_DOCS_URL}`
    );
  }

  const cacheKey = username.toLowerCase();
  const cached = zoneUrlCache.get(cacheKey);
  if (cached) {
    return cached;
  }

  const lookupUrl = `${ZONE_INFO_URL}?user=${encodeURIComponent(username)}`;
  let response: Response;
  try {
    response = await fetchImpl(lookupUrl, {
      method: 'GET',
      headers: { Accept: 'application/json' }
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    logger.error(
      `Failed to contact Autotask zone info endpoint: ${message}. ` +
        `Set AUTOTASK_API_URL manually — see ${ZONE_DOCS_URL}`
    );
    throw new Error(
      `Autotask zone auto-detection failed (network error: ${message}). ` +
        `Set AUTOTASK_API_URL manually — see ${ZONE_DOCS_URL}`,
      { cause: err }
    );
  }

  if (!response.ok) {
    logger.error(
      `Autotask zone info endpoint returned HTTP ${response.status} for user ${username}. ` +
        `Verify the username (API user email) is correct, or set AUTOTASK_API_URL manually — see ${ZONE_DOCS_URL}`
    );
    throw new Error(
      `Autotask zone auto-detection failed (HTTP ${response.status}). ` +
        `Set AUTOTASK_API_URL manually — see ${ZONE_DOCS_URL}`
    );
  }

  let body: any;
  try {
    body = await response.json();
  } catch (err) {
    logger.error(
      `Autotask zone info response was not valid JSON. ` +
        `Set AUTOTASK_API_URL manually — see ${ZONE_DOCS_URL}`
    );
    throw new Error(
      `Autotask zone auto-detection failed (malformed response). ` +
        `Set AUTOTASK_API_URL manually — see ${ZONE_DOCS_URL}`,
      { cause: err }
    );
  }

  const url: unknown = body?.url;
  if (typeof url !== 'string' || url.length === 0) {
    logger.error(
      `Autotask zone info response missing "url" field. ` +
        `Set AUTOTASK_API_URL manually — see ${ZONE_DOCS_URL}`
    );
    throw new Error(
      `Autotask zone auto-detection failed (missing url in response). ` +
        `Set AUTOTASK_API_URL manually — see ${ZONE_DOCS_URL}`
    );
  }

  const zoneName = typeof body?.zoneName === 'string' ? body.zoneName : 'unknown';
  logger.info(`Auto-detected Autotask zone "${zoneName}" for user ${username}: ${url}`);

  zoneUrlCache.set(cacheKey, url);
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
 * Which access path a gateway request came through, set by APIM as a static
 * X-Access-Channel header per API/product:
 *   - "employee": the Entra-authenticated, per-employee API. Write tool
 *     calls are gated on a resolved Phase 7 impersonation match (see
 *     WriteGuardConfig in tool.handler.ts).
 *   - "automation": a separate, subscription-key-secured API intended for
 *     administratively managed integrations (e.g. Make.com scenarios). No
 *     employee identity, no impersonation auto-resolution attempt, and no
 *     write gate -- these run under the API-only user's own identity,
 *     exactly like any other direct Autotask API integration.
 */
export type AccessChannel = 'employee' | 'automation';

/**
 * Determine the access channel for this request from the X-Access-Channel
 * header. Defaults to the more restrictive "employee" behavior if the
 * header is missing or holds an unrecognized value, so a misconfigured or
 * bypassed APIM policy fails closed (gated) rather than silently granting
 * unrestricted automation-level access.
 */
export function getAccessChannelFromHeaders(headers: Record<string, string | string[] | undefined>): AccessChannel {
  const value = headers['x-access-channel'] || headers['X-Access-Channel'];
  const raw = Array.isArray(value) ? value[0] : value;
  return raw === 'automation' ? 'automation' : 'employee';
}

/**
 * Get configuration help text
 */
export function getConfigHelp(): string {
  return `
Autotask MCP Server Configuration:

=== Local Mode (default) ===
Required Environment Variables:
  AUTOTASK_USERNAME         - Autotask API username (email)
  AUTOTASK_SECRET          - Autotask API secret key
  AUTOTASK_INTEGRATION_CODE - Autotask integration code

=== Gateway Mode (hosted deployment) ===
When AUTH_MODE=gateway, credentials are injected by the MCP Gateway:
  X_API_KEY                - Autotask API username (from X-API-Key header)
  X_API_SECRET             - Autotask API secret (from X-API-Secret header)
  X_INTEGRATION_CODE       - Autotask integration code (from X-Integration-Code header)
  GATEWAY_SHARED_SECRET     - Optional. If set, every POST /mcp request must carry a matching
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
  X-Access-Channel (header) - Optional, not an env var. "employee" (default if missing/unrecognized)
                              or "automation". On "employee", write tool calls (create/update/delete,
                              or autotask_raw_request with a non-GET method) are rejected unless a
                              Phase 7 impersonation match was resolved (or X-Impersonation-Resource-Id
                              was explicitly set). On "automation", no impersonation is attempted and
                              no write gate applies -- intended for administratively managed
                              integrations (e.g. Make.com), secured separately (e.g. an APIM
                              subscription key) rather than by employee login.

=== Common Options ===
  AUTOTASK_API_URL         - Autotask API base URL (auto-detected if not provided)
  AUTH_MODE                - Authentication mode: env (default), gateway
  MCP_SERVER_NAME          - Server name (default: autotask-mcp)
  MCP_SERVER_VERSION       - Override the reported server version. Defaults to the version baked into the image's package.json at build time. Useful for stamping a custom build identifier.
  MCP_TRANSPORT            - Transport type: stdio, http (default: stdio)
  MCP_HTTP_PORT            - HTTP port when using http transport (default: 8080)
  MCP_HTTP_HOST            - HTTP host when using http transport (default: 0.0.0.0)
  LOG_LEVEL                - Logging level: error, warn, info, debug (default: info)
  LOG_FORMAT               - Log format: simple, json (default: simple)
  AUTOTASK_ENHANCE_CONCURRENCY - Max concurrent Autotask API calls used to resolve company/resource names on search results (default: 3). Kept low to stay under Autotask's concurrent-thread limit.

Example (Local Mode):
  AUTOTASK_USERNAME=api-user@example.com
  AUTOTASK_SECRET=your-secret-key
  AUTOTASK_INTEGRATION_CODE=your-integration-code

Example (Gateway Mode):
  AUTH_MODE=gateway
  MCP_TRANSPORT=http
  # Credentials injected by gateway via X-API-Key, X-API-Secret, X-Integration-Code headers
`.trim();
}
