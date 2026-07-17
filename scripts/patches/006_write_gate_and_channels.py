#!/usr/bin/env python3
"""
Patch: Write gate for the "employee" access channel + employee/automation
channel distinction.

Design (confirmed with Mathias on 13.07.2026 -- see Fortschritts_Log.md):
  - Two access channels through APIM, distinguished by a static
    X-Access-Channel header set per API/product:
      - "employee": the existing Entra-authenticated, per-employee API.
        Write tool calls (create/update/delete, or autotask_raw_request
        with a non-GET method) are BLOCKED unless a Phase 7 impersonation
        match was resolved for the caller (or X-Impersonation-Resource-Id
        was explicitly set). Reads are never gated -- Autotask's REST API
        does not filter reads by the impersonated resource's own
        permissions anyway (confirmed against official docs: impersonation
        only affects write attribution), so gating reads would add
        friction without adding real access control.
      - "automation": a separate, subscription-key-secured API intended
        for administratively managed integrations (e.g. Make.com). No
        impersonation auto-resolution is attempted and no write gate
        applies -- these run under the API-only user's own identity,
        same as any other direct Autotask API integration.
    Missing/unrecognized X-Access-Channel defaults to "employee" (the
    more restrictive behavior), so a misconfigured or bypassed APIM
    policy fails closed rather than silently granting unrestricted
    automation-level access.
  - Per-entity research (13.07.2026, web research covering the official
    Autotask REST API entity docs) found that several entities used by our
    write tools do NOT support ImpersonationResourceId at all (no
    `impersonatorCreatorResourceID` field in their docs): TicketCharges,
    ServiceCallTickets, ServiceCallTicketResources, Contracts,
    ContractServices, Tasks (already empirically known), Phases,
    TicketChecklistItems, ExpenseReports, ExpenseItems, QuoteItems.
    Decision: the write gate still applies uniformly to ALL write tools
    regardless of per-entity impersonation support. The gate is an access
    control on OUR side (does this caller map to a real, current Autotask
    employee), not dependent on whether Autotask itself can display
    correct "created by" attribution. For the entities above, a
    successfully-gated write will still show "API User" as creator in
    Autotask's UI -- that's an Autotask documentation/UI limitation, not a
    bug in this gate.

Adds:
  - src/utils/config.ts:
      - AccessChannel type ("employee" | "automation").
      - getAccessChannelFromHeaders(): reads X-Access-Channel, defaults to
        "employee".
  - src/handlers/tool.handler.ts:
      - WriteGuardConfig interface ({ channel, impersonationResolved }).
      - isWriteToolCall(name, args): true if the call would mutate data.
        Handles autotask_raw_request (classified by args.method) and
        autotask_execute_tool (classified recursively by args.toolName)
        specially; everything else matches /^autotask_(create|update|delete)_/.
      - AutotaskToolHandler constructor takes an optional WriteGuardConfig;
        callTool() rejects with an "impersonation_required" error (without
        executing) when channel is "employee", no impersonation was
        resolved, and the call is a write.
  - src/mcp/server.ts:
      - buildPerRequestHandlers() takes an optional WriteGuardConfig,
        passed through to the AutotaskToolHandler constructor.
      - The gateway branch in startHttpTransport() reads the access
        channel and, only on "employee", performs the Phase 7 impersonation
        lookup added in patch 005 -- now tracking whether it actually
        resolved a match (boolean), not just attempting it. That boolean
        (or "true" if a manual X-Impersonation-Resource-Id override was
        already present) becomes WriteGuardConfig.impersonationResolved.
        On "automation", the lookup is skipped entirely.

Requires patch 005 (005_phase7_impersonation_resolution.py) to already be
applied -- this patch's anchors are the post-005 state of config.ts and
server.ts.

Does NOT touch:
  - APIM: the employee-facing API's policy should add an explicit
    <set-header name="X-Access-Channel"><value>employee</value></set-header>
    (defense in depth -- the code already defaults missing/unrecognized
    values to "employee", but making it explicit avoids relying on that
    default). Creating the second "automation" API/product (subscription
    key, Make.com-facing) is a separate, not-yet-scheduled infra step --
    the code path is ready for it whenever that's built.

Tested on 13.07.2026: applied cleanly against a sandbox reconstruction of
the post-005 src/utils/config.ts and src/mcp/server.ts (both verified
against the real fetched/patched content), and against a minimal skeleton
of src/handlers/tool.handler.ts preserving the exact anchor regions from
the real fetched file (the omitted middle section -- the per-entity
dispatch table -- is untouched by this patch and wasn't needed for
verification). Brace/paren/bracket balance verified in all three patched
files. tsc/npm test could not be run in this sandbox (no npm registry
access) -- run both after applying, before committing.

Run from the repo root (after 005 has been applied):
    python3 scripts/patches/006_write_gate_and_channels.py

Idempotent: safe to run multiple times, and safe to run after the files
have already been patched (each edit checks first).
"""

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

CONFIG_TS = REPO_ROOT / "src" / "utils" / "config.ts"
SERVER_TS = REPO_ROOT / "src" / "mcp" / "server.ts"
TOOL_HANDLER_TS = REPO_ROOT / "src" / "handlers" / "tool.handler.ts"


def replace_once(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")

    if new in text:
        print(f"[skip] {label}: already applied")
        return

    if old not in text:
        print(f"[FAIL] {label}: expected original text not found in {path}")
        print("       File may have changed since this patch was written (or patch 005 isn't applied yet) -- inspect manually.")
        sys.exit(1)

    count = text.count(old)
    if count != 1:
        print(f"[FAIL] {label}: expected exactly 1 match, found {count} in {path}")
        sys.exit(1)

    path.write_text(text.replace(old, new), encoding="utf-8")
    print(f"[ok]   {label}")


def patch_config_ts() -> None:
    replace_once(
        CONFIG_TS,
        old="""export function getUserPrincipalFromHeaders(headers: Record<string, string | string[] | undefined>): string | undefined {
  const value = headers['x-user-principal'] || headers['X-User-Principal'];
  return Array.isArray(value) ? value[0] : value;
}

/**
 * Get configuration help text
 */
export function getConfigHelp(): string {""",
        new="""export function getUserPrincipalFromHeaders(headers: Record<string, string | string[] | undefined>): string | undefined {
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
export function getConfigHelp(): string {""",
        label="config.ts: AccessChannel type + getAccessChannelFromHeaders()",
    )

    replace_once(
        CONFIG_TS,
        old="""  X-User-Principal (header) - Optional, not an env var. If present and X-Impersonation-Resource-Id
                              is not explicitly set, the server resolves this header's local part
                              (before "@") against Autotask's Resources.userName field
                              (case-insensitive exact match) and uses the match, if any, as the
                              impersonation target for this request. Intended to be injected by
                              APIM from the validated Entra token's "upn" claim. No match -> the
                              request proceeds without impersonation (fails open).

=== Common Options ===""",
        new="""  X-User-Principal (header) - Optional, not an env var. If present and X-Impersonation-Resource-Id
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

=== Common Options ===""",
        label="config.ts: getConfigHelp() documents X-Access-Channel header",
    )


def patch_tool_handler_ts() -> None:
    replace_once(
        TOOL_HANDLER_TS,
        old="""export interface McpToolResult {
  content: Array<{
    type: string;
    text: string;
  }>;
  isError?: boolean;
}

export class AutotaskToolHandler {""",
        new="""export interface McpToolResult {
  content: Array<{
    type: string;
    text: string;
  }>;
  isError?: boolean;
}

/**
 * Phase 7 write gate configuration, built per-request in gateway mode (see
 * AutotaskMcpServer.buildPerRequestHandlers). Undefined in env/local mode
 * and on the "automation" access channel -- no gate applies in either case.
 */
export interface WriteGuardConfig {
  channel: 'employee' | 'automation';
  impersonationResolved: boolean;
}

/**
 * True if calling `name` with `args` would mutate Autotask data (create,
 * update, or delete), used to decide whether the Phase 7 write gate applies.
 *
 * Two tools need special handling instead of a simple name check:
 *   - autotask_raw_request: an escape hatch with a caller-supplied HTTP
 *     method (args.method). Classified by that method, not by the tool name.
 *   - autotask_execute_tool: the lazy-loading meta-tool that dispatches to
 *     another tool by name (args.toolName). Classified recursively by the
 *     actual target tool, otherwise every write in lazy-loading mode would
 *     bypass the gate entirely.
 */
function isWriteToolCall(name: string, args: Record<string, any>): boolean {
  if (name === 'autotask_execute_tool') {
    const innerName = args?.toolName;
    if (typeof innerName !== 'string') return false;
    return isWriteToolCall(innerName, args?.arguments || {});
  }
  if (name === 'autotask_raw_request') {
    const method = String(args?.method || 'GET').toUpperCase();
    return method !== 'GET';
  }
  return /^autotask_(create|update|delete)_/.test(name);
}

export class AutotaskToolHandler {""",
        label="tool.handler.ts: WriteGuardConfig + isWriteToolCall()",
    )

    replace_once(
        TOOL_HANDLER_TS,
        old="""  private mappingService: MappingService | null = null;
  private lazyLoading: boolean;
  private enhanceConcurrency: number;

  constructor(autotaskService: AutotaskService, logger: Logger, lazyLoading = false) {
    this.autotaskService = autotaskService;
    this.logger = logger;
    this.lazyLoading = lazyLoading;
    this.enhanceConcurrency = resolveEnhanceConcurrency(process.env.AUTOTASK_ENHANCE_CONCURRENCY);""",
        new="""  private mappingService: MappingService | null = null;
  private lazyLoading: boolean;
  private enhanceConcurrency: number;
  private writeGuard?: WriteGuardConfig;

  constructor(autotaskService: AutotaskService, logger: Logger, lazyLoading = false, writeGuard?: WriteGuardConfig) {
    this.autotaskService = autotaskService;
    this.logger = logger;
    this.lazyLoading = lazyLoading;
    this.writeGuard = writeGuard;
    this.enhanceConcurrency = resolveEnhanceConcurrency(process.env.AUTOTASK_ENHANCE_CONCURRENCY);""",
        label="tool.handler.ts: constructor accepts WriteGuardConfig",
    )

    replace_once(
        TOOL_HANDLER_TS,
        old="""  async callTool(name: string, args: Record<string, any>): Promise<McpToolResult> {
    this.logger.debug(`Calling tool: ${name}`, args);

    try {""",
        new="""  async callTool(name: string, args: Record<string, any>): Promise<McpToolResult> {
    this.logger.debug(`Calling tool: ${name}`, args);

    if (this.writeGuard?.channel === 'employee' && !this.writeGuard.impersonationResolved && isWriteToolCall(name, args)) {
      this.logger.warn(`Blocked write tool call "${name}": no Autotask account resolved for the authenticated employee.`);
      return errorToolResult({
        error: 'Write operations require an Autotask account for the authenticated employee. No matching Autotask resource was found for your identity, so this write was blocked.',
        error_type: 'impersonation_required',
        tool: name,
      });
    }

    try {""",
        label="tool.handler.ts: callTool() enforces the write gate",
    )


def patch_server_ts() -> None:
    replace_once(
        SERVER_TS,
        old="""import {
  EnvironmentConfig,
  parseCredentialsFromHeaders,
  GatewayCredentials,
  getServerVersion,
  resolveAutotaskApiUrl,
  resolveImpersonationResourceIdFromPrincipal,
  getUserPrincipalFromHeaders,
} from '../utils/config.js';""",
        new="""import {
  EnvironmentConfig,
  parseCredentialsFromHeaders,
  GatewayCredentials,
  getServerVersion,
  resolveAutotaskApiUrl,
  resolveImpersonationResourceIdFromPrincipal,
  getUserPrincipalFromHeaders,
  getAccessChannelFromHeaders,
} from '../utils/config.js';""",
        label="server.ts: import getAccessChannelFromHeaders",
    )

    replace_once(
        SERVER_TS,
        old="""import { AutotaskToolHandler } from '../handlers/tool.handler.js';""",
        new="""import { AutotaskToolHandler, WriteGuardConfig } from '../handlers/tool.handler.js';""",
        label="server.ts: import WriteGuardConfig",
    )

    replace_once(
        SERVER_TS,
        old="""  private buildPerRequestHandlers(credentials: GatewayCredentials): {
    toolHandler: AutotaskToolHandler;
    resourceHandler: AutotaskResourceHandler;
  } {
    const autotaskConfig: McpServerConfig['autotask'] = {};
    if (credentials.username) autotaskConfig.username = credentials.username;
    if (credentials.secret) autotaskConfig.secret = credentials.secret;
    if (credentials.integrationCode) autotaskConfig.integrationCode = credentials.integrationCode;
    if (credentials.apiUrl) autotaskConfig.apiUrl = credentials.apiUrl;
    if (credentials.impersonationResourceId) autotaskConfig.impersonationResourceId = credentials.impersonationResourceId;

    const requestConfig: McpServerConfig = {
      name: this.envConfig?.server?.name || 'autotask-mcp',
      version: getServerVersion(this.envConfig?.server?.version),
      autotask: autotaskConfig,
    };

    const service = new AutotaskService(requestConfig, this.logger);
    return {
      resourceHandler: new AutotaskResourceHandler(service, this.logger),
      toolHandler: new AutotaskToolHandler(service, this.logger, this.lazyLoading),
    };
  }""",
        new="""  private buildPerRequestHandlers(credentials: GatewayCredentials, writeGuard?: WriteGuardConfig): {
    toolHandler: AutotaskToolHandler;
    resourceHandler: AutotaskResourceHandler;
  } {
    const autotaskConfig: McpServerConfig['autotask'] = {};
    if (credentials.username) autotaskConfig.username = credentials.username;
    if (credentials.secret) autotaskConfig.secret = credentials.secret;
    if (credentials.integrationCode) autotaskConfig.integrationCode = credentials.integrationCode;
    if (credentials.apiUrl) autotaskConfig.apiUrl = credentials.apiUrl;
    if (credentials.impersonationResourceId) autotaskConfig.impersonationResourceId = credentials.impersonationResourceId;

    const requestConfig: McpServerConfig = {
      name: this.envConfig?.server?.name || 'autotask-mcp',
      version: getServerVersion(this.envConfig?.server?.version),
      autotask: autotaskConfig,
    };

    const service = new AutotaskService(requestConfig, this.logger);
    return {
      resourceHandler: new AutotaskResourceHandler(service, this.logger),
      toolHandler: new AutotaskToolHandler(service, this.logger, this.lazyLoading, writeGuard),
    };
  }""",
        label="server.ts: buildPerRequestHandlers() accepts WriteGuardConfig",
    )

    replace_once(
        SERVER_TS,
        old="""        // In gateway mode, build per-request service + handlers from the
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
          } else {""",
        new="""        // In gateway mode, build per-request service + handlers from the
        // injected credential headers. Each request gets its own isolated
        // AutotaskService so concurrent requests for different tenants
        // never interfere with each other.
        if (isGatewayMode) {
          const credentials = parseCredentialsFromHeaders(req.headers as Record<string, string | string[] | undefined>);
          if (credentials.username && credentials.secret && credentials.integrationCode) {
            const channel = getAccessChannelFromHeaders(req.headers as Record<string, string | string[] | undefined>);

            // Phase 7 impersonation auto-resolution, and the write gate that
            // depends on it, only apply on the "employee" channel. On
            // "automation" (Make.com etc.) requests run under the API-only
            // user's own identity with no impersonation attempt and no gate.
            const userPrincipal = channel !== 'employee' || credentials.impersonationResourceId
              ? undefined
              : getUserPrincipalFromHeaders(req.headers as Record<string, string | string[] | undefined>);

            const impersonationLookup: Promise<boolean> = userPrincipal
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
                      return true;
                    }
                    this.logger.warn(`No Autotask resource matched principal "${userPrincipal}" -- write tool calls will be blocked.`);
                    return false;
                  })
                  .catch((err) => {
                    this.logger.error('Impersonation resource-ID lookup failed -- write tool calls will be blocked:', err);
                    return false;
                  })
              : Promise.resolve(!!credentials.impersonationResourceId);

            impersonationLookup.then((impersonationResolved) => {
              const handlers = this.buildPerRequestHandlers(credentials, { channel, impersonationResolved });
              this.dispatchMcpRequest(req, res, handlers.toolHandler, handlers.resourceHandler);
            });
          } else {""",
        label="server.ts: gateway branch computes channel + tracks impersonationResolved",
    )


def main() -> None:
    for path in (CONFIG_TS, SERVER_TS, TOOL_HANDLER_TS):
        if not path.exists():
            print(f"[FAIL] {path} not found -- run this script from the repo root.")
            sys.exit(1)

    patch_config_ts()
    patch_tool_handler_ts()
    patch_server_ts()
    print("\nDone. Review with `git diff`, then run the test suite before committing.")


if __name__ == "__main__":
    main()
