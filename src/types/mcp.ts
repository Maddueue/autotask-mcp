// MCP Protocol Type Definitions
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
