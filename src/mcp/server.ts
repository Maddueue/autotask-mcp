// Main MCP Server Implementation
// Handles the Model Context Protocol server setup and integration with Autotask
// Supports both local (env-based) and gateway (header-based) credential modes

import { createServer, IncomingMessage, ServerResponse, Server as HttpServer } from 'node:http';
import { createHash, timingSafeEqual } from 'node:crypto';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { Transport } from '@modelcontextprotocol/sdk/shared/transport.js';
import {
  CallToolRequestSchema,
  ErrorCode,
  ListResourcesRequestSchema,
  ListToolsRequestSchema,
  McpError,
  ReadResourceRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';

import { AutotaskService } from '../services/autotask.service.js';
import { Logger } from '../utils/logger.js';
import { McpServerConfig } from '../types/mcp.js';
import {
  EnvironmentConfig,
  parseCredentialsFromHeaders,
  GatewayCredentials,
  getServerVersion,
  resolveAutotaskApiUrl,
  resolveImpersonationResourceIdFromPrincipal,
  getUserPrincipalFromHeaders,
  getAccessChannelFromHeaders,
} from '../utils/config.js';
import { AutotaskResourceHandler } from '../handlers/resource.handler.js';
import { AutotaskToolHandler, WriteGuardConfig } from '../handlers/tool.handler.js';
import { registerPromptHandlers } from './prompts.js';

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

export class AutotaskMcpServer {
  private server: Server;
  private config: McpServerConfig;
  private autotaskService: AutotaskService;
  private resourceHandler: AutotaskResourceHandler;
  private toolHandler: AutotaskToolHandler;
  private logger: Logger;
  private envConfig: EnvironmentConfig | undefined;
  private httpServer?: HttpServer;
  private lazyLoading: boolean;

  constructor(config: McpServerConfig, logger: Logger, envConfig?: EnvironmentConfig) {
    this.logger = logger;
    this.config = config;
    this.envConfig = envConfig;

    // Initialize Autotask service
    this.autotaskService = new AutotaskService(config, logger);
    this.lazyLoading = envConfig?.lazyLoading ?? false;

    // Initialize handlers
    this.resourceHandler = new AutotaskResourceHandler(this.autotaskService, logger);
    this.toolHandler = new AutotaskToolHandler(this.autotaskService, logger, this.lazyLoading);

    // Create default server (used for stdio mode)
    this.server = this.createFreshServer();
  }

  /**
   * Create a fresh MCP Server with all handlers registered.
   * Called per-request in HTTP (stateless) mode so each initialize gets a clean server.
   *
   * In gateway mode, per-request handlers are passed so each request is fully
   * isolated — no shared mutable state between concurrent requests.
   */
  private createFreshServer(
    perRequestToolHandler?: AutotaskToolHandler,
    perRequestResourceHandler?: AutotaskResourceHandler,
  ): Server {
    const server = new Server(
      {
        name: this.config.name,
        version: this.config.version,
      },
      {
        capabilities: {
          resources: {
            subscribe: false,
            listChanged: true
          },
          tools: {
            listChanged: true
          },
          prompts: {
            listChanged: false
          }
        },
        instructions: this.getServerInstructions()
      }
    );

    server.onerror = (error) => {
      this.logger.error('MCP Server error:', error);
    };

    server.oninitialized = () => {
      this.logger.info('MCP Server initialized and ready to serve requests');
    };

    const toolHandler = perRequestToolHandler ?? this.toolHandler;
    const resourceHandler = perRequestResourceHandler ?? this.resourceHandler;
    this.setupHandlers(server, toolHandler, resourceHandler);
    toolHandler.setServer(server);

    return server;
  }

  /**
   * Build per-request service + handlers from gateway credentials.
   * Returns fully isolated instances that won't be affected by concurrent requests.
   */
  private buildPerRequestHandlers(credentials: GatewayCredentials, writeGuard?: WriteGuardConfig): {
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
   * Build a fresh MCP `Server` for a single request, optionally bound to
   * per-request gateway credentials.
   *
   * This is the reuse seam for non-Node transports (e.g. the Cloudflare
   * Workers entrypoint in `worker.ts`), which cannot use the Node
   * `http.createServer` HTTP path but still need the exact same handler
   * wiring. When `credentials` carry a full username/secret/integrationCode
   * triple, an isolated per-request service + handlers are created; otherwise
   * the default (env-configured) handlers are used.
   */
  public createRequestServer(credentials?: GatewayCredentials): Server {
    if (
      credentials &&
      credentials.username &&
      credentials.secret &&
      credentials.integrationCode
    ) {
      const { toolHandler, resourceHandler } =
        this.buildPerRequestHandlers(credentials);
      return this.createFreshServer(toolHandler, resourceHandler);
    }
    return this.createFreshServer();
  }

  /**
   * Set up all MCP request handlers
   */
  private setupHandlers(
    server: Server,
    toolHandler: AutotaskToolHandler,
    resourceHandler: AutotaskResourceHandler,
  ): void {
    this.logger.info('Setting up MCP request handlers...');

    // List available resources
    server.setRequestHandler(ListResourcesRequestSchema, async () => {
      try {
        this.logger.debug('Handling list resources request');
        const resources = await resourceHandler.listResources();
        return { resources };
      } catch (error) {
        this.logger.error('Failed to list resources:', error);
        throw new McpError(
          ErrorCode.InternalError,
          `Failed to list resources: ${error instanceof Error ? error.message : 'Unknown error'}`
        );
      }
    });

    // Read a specific resource
    server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
      try {
        this.logger.debug(`Handling read resource request for: ${request.params.uri}`);
        const content = await resourceHandler.readResource(request.params.uri);
        return { contents: [content] };
      } catch (error) {
        this.logger.error(`Failed to read resource ${request.params.uri}:`, error);
        throw new McpError(
          ErrorCode.InternalError,
          `Failed to read resource: ${error instanceof Error ? error.message : 'Unknown error'}`
        );
      }
    });

    // List available tools
    server.setRequestHandler(ListToolsRequestSchema, async () => {
      try {
        this.logger.debug('Handling list tools request');
        const tools = await toolHandler.listTools();
        return { tools };
      } catch (error) {
        this.logger.error('Failed to list tools:', error);
        throw new McpError(
          ErrorCode.InternalError,
          `Failed to list tools: ${error instanceof Error ? error.message : 'Unknown error'}`
        );
      }
    });

    // Call a tool
    server.setRequestHandler(CallToolRequestSchema, async (request) => {
      try {
        this.logger.debug(`Handling tool call: ${request.params.name}`);
        const result = await toolHandler.callTool(
          request.params.name,
          request.params.arguments || {}
        );
        return {
          content: result.content,
          isError: result.isError
        };
      } catch (error) {
        this.logger.error(`Failed to call tool ${request.params.name}:`, error);
        throw new McpError(
          ErrorCode.InternalError,
          `Failed to call tool: ${error instanceof Error ? error.message : 'Unknown error'}`
        );
      }
    });

    // Register prompt handlers
    registerPromptHandlers(server);

    this.logger.info('MCP request handlers set up successfully');
  }

  /**
   * Start the MCP server with the configured transport
   */
  async start(): Promise<void> {
    const transportType = this.envConfig?.transport?.type || 'stdio';
    this.logger.info(`Starting Autotask MCP Server with ${transportType} transport...`);

    if (transportType === 'http') {
      await this.startHttpTransport();
    } else {
      await this.startStdioTransport();
    }
  }

  /**
   * Start with stdio transport (default)
   */
  private async startStdioTransport(): Promise<void> {
    const transport = new StdioServerTransport();
    await this.server.connect(transport);
    this.logger.info('Autotask MCP Server started and connected to stdio transport');
  }

  /**
   * Start with HTTP Streamable transport
   * In gateway mode, credentials are extracted from request headers on each request
   */
  private async startHttpTransport(): Promise<void> {
    const port = this.envConfig?.transport?.port || 8080;
    const host = this.envConfig?.transport?.host || '0.0.0.0';
    const isGatewayMode = this.envConfig?.auth?.mode === 'gateway';
    const gatewaySharedSecret = this.envConfig?.auth?.gatewaySharedSecret;

    this.httpServer = createServer((req: IncomingMessage, res: ServerResponse) => {
      const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);

      // CORS headers applied to every response so browser-based MCP clients
      // (e.g. claude.ai custom connectors) can reach the server. '*' is safe
      // because credentials are carried via request headers, not cookies.
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, DELETE');
      res.setHeader(
        'Access-Control-Allow-Headers',
        'Content-Type, Accept, Authorization, Mcp-Session-Id, X-API-Key, X-API-Secret, X-Integration-Code, X-Impersonation-Resource-Id, X-User-Principal'
      );
      res.setHeader('Access-Control-Max-Age', '86400');

      // CORS preflight — respond before routing so every path (including /mcp)
      // answers OPTIONS with 204 and the headers above.
      if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
      }

      // Health endpoint - no auth required
      if (url.pathname === '/health') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        // `mcpTransport` (not `transport`) to avoid confusion with the network
        // scheme — the value refers to the MCP transport type (stdio vs
        // Streamable HTTP), not whether the service is served over HTTP/HTTPS.
        // `version` is included so operators can curl-check which build is
        // running without going through the MCP handshake.
        res.end(JSON.stringify({
          status: 'ok',
          version: getServerVersion(this.envConfig?.server?.version),
          mcpTransport: 'http',
          authMode: isGatewayMode ? 'gateway' : 'env',
          timestamp: new Date().toISOString()
        }));
        return;
      }

      // MCP endpoint — stateless: fresh server + transport per request
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
        if (req.method !== 'POST') {
          res.writeHead(405, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({
            jsonrpc: '2.0',
            error: { code: -32000, message: 'Method not allowed' },
            id: null,
          }));
          return;
        }

        // In gateway mode, build per-request service + handlers from the
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
      }

      // 404 for everything else
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Not found', endpoints: ['/mcp', '/health'] }));
    });

    await new Promise<void>((resolve) => {
      this.httpServer!.listen(port, host, () => {
        this.logger.info(`Autotask MCP Server listening on http://${host}:${port}/mcp`);
        this.logger.info(`Health check available at http://${host}:${port}/health`);
        this.logger.info(`Authentication mode: ${isGatewayMode ? 'gateway (header-based)' : 'env (environment variables)'}`);
        resolve();
      });
    });
  }

  /**
   * Stop the server gracefully
   */
  async stop(): Promise<void> {
    this.logger.info('Stopping Autotask MCP Server...');
    if (this.httpServer) {
      await new Promise<void>((resolve, reject) => {
        this.httpServer!.close((err) => err ? reject(err) : resolve());
      });
    }
    await this.server.close();
    this.logger.info('Autotask MCP Server stopped');
  }

  /**
   * Get server instructions for clients
   */
  private getServerInstructions(): string {
    return `
# Autotask MCP Server

This server provides access to Kaseya Autotask PSA data and operations through the Model Context Protocol.

## Available Resources:
- **autotask://companies/{id}** - Get company details by ID
- **autotask://companies** - List all companies
- **autotask://contacts/{id}** - Get contact details by ID  
- **autotask://contacts** - List all contacts
- **autotask://tickets/{id}** - Get ticket details by ID
- **autotask://tickets** - List all tickets

## Progressive Discovery (Lazy Loading):
When LAZY_LOADING=true, only 3 meta-tools are exposed initially:
- **autotask_list_categories** - List all available tool categories with descriptions and tool counts
- **autotask_list_category_tools** - Get full tool schemas for a specific category
- **autotask_execute_tool** - Execute any tool by name with arguments (used in lazy loading mode)

Use autotask_list_categories to discover available tool categories, then autotask_list_category_tools to get full schemas for a category, then autotask_execute_tool to call the desired tool.

## Available Tools (39 total):
- Companies: search, create, update
- Contacts: search, create
- Tickets: search, get details, create
- Time entries: create
- Projects: search, create
- Resources: search
- Notes: get/search/create for tickets, projects, companies
- Attachments: get/search ticket attachments
- Financial: expense reports, quotes, quote items (CRUD), invoices, contracts
- Sales: opportunities, products, services, service bundles
- Configuration items: search
- Tasks: search, create
- Picklists: list queues, list ticket statuses, list ticket priorities, get field info
- Utility: test connection

## Picklist Discovery:
Use autotask_list_queues, autotask_list_ticket_statuses, or autotask_list_ticket_priorities to discover valid IDs before filtering. Use autotask_get_field_info for any entity's field definitions and picklist values.

## ID-to-Name Mapping:
All search and detail tools automatically include human-readable names for company and resource IDs in an _enhanced field on each result.

## Authentication:
This server requires valid Autotask API credentials. Ensure you have:
- AUTOTASK_USERNAME (API user email)
- AUTOTASK_SECRET (API secret key)
- AUTOTASK_INTEGRATION_CODE (integration code)

For more information, visit: https://github.com/wyre-technology/autotask-mcp
`.trim();
  }
}