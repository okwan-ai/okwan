/**
 * How each MCP client reaches the hosted server. There is one URL and one
 * header (packages/query/okwan_query/mcp_http.py `_bearer`); what varies
 * per merchant is the key, never the client. Every snippet keeps the
 * `okw_…` placeholder: a key is shown once at issue and no page holds one.
 *
 * Pure: strings only, so the client tabs and the navy panel share it.
 *
 * Client command and file forms come from the vendors' docs, not this
 * repo, and change between releases. Each lives here once so a fix is one
 * line. The Claude Desktop pitfalls are first-hand (OKWAN_PROJECT.md §11).
 */

import { HOSTED_TOOL_NAMES } from "./hosted-tools";

export type McpClient = {
  id: string;
  label: string;
  /** How it reaches the server, in a few words: the tab strip is the catalog. */
  via: string;
  /** What must be in place first. */
  needs: string;
  /** Numbered steps. `**…**` marks the client's own UI nouns; `` `…` `` is code. */
  steps: string[];
  snippetLabel: string;
  snippet: (apiBase: string, server: string) => string;
  /** Known failure modes, shown where they bite. */
  pitfalls?: string[];
  docs: { label: string; href: string };
};

function mcpUrl(apiBase: string): string {
  return `${apiBase}/mcp/`;
}

/**
 * Claude Desktop reaches a remote server through mcp-remote. The header is
 * passed as one argument with no space anywhere in it: Claude Desktop has
 * mangled spaces inside args, and mcp-remote drops a `--header` that is not
 * exact `Name:Value` (§11). The value, which needs a space, comes from env.
 */
export function claudeDesktopConfig(apiBase: string, server: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        [server]: {
          command: "npx",
          args: ["mcp-remote", mcpUrl(apiBase), "--header", "Authorization:${OKWAN_AUTH}"],
          env: { OKWAN_AUTH: "Bearer okw_…" },
        },
      },
    },
    null,
    2,
  );
}

export const MCP_CLIENTS: McpClient[] = [
  {
    id: "claude-code",
    label: "Claude Code",
    via: "one command; native HTTP with a header",
    needs: "The Claude Code CLI, signed in.",
    steps: [
      "In a terminal where **Claude Code** is installed, run the command with the key in place of `okw_…`. Add `--scope user` to keep the server across projects.",
      "Start `claude` and type **/mcp**: the server shows as connected, with its four tools.",
      "Ask it the question below.",
    ],
    snippetLabel: "Terminal",
    snippet: (apiBase, server) =>
      [`claude mcp add --transport http ${server} ${mcpUrl(apiBase)} \\`, `  --header "Authorization: Bearer okw_…"`].join("\n"),
    docs: { label: "Claude Code: MCP", href: "https://docs.claude.com/en/docs/claude-code/mcp" },
  },
  {
    id: "cursor",
    label: "Cursor",
    via: "url and headers in mcp.json",
    needs: "Cursor, with MCP enabled in its settings.",
    steps: [
      "Open **Cursor Settings → MCP** and choose **Add new MCP server**, or create `.cursor/mcp.json` in the project (`~/.cursor/mcp.json` to use it from every project).",
      "Paste the server with the key in place of `okw_…` and save. The MCP list shows it with four tools.",
      "Ask in **Agent** mode.",
    ],
    snippetLabel: ".cursor/mcp.json",
    snippet: (apiBase, server) =>
      JSON.stringify(
        { mcpServers: { [server]: { url: mcpUrl(apiBase), headers: { Authorization: "Bearer okw_…" } } } },
        null,
        2,
      ),
    docs: { label: "Cursor: MCP", href: "https://cursor.com/docs/context/mcp" },
  },
  {
    id: "claude-desktop",
    label: "Claude Desktop",
    via: "via mcp-remote, a local bridge",
    needs: "Node.js 18 or newer on the machine: Claude Desktop runs mcp-remote, which bridges the hosted URL to a local process.",
    steps: [
      "Open **Settings → Developer** and choose **Edit Config**; it opens `claude_desktop_config.json`.",
      "Paste the server into `mcpServers`, with the key in place of `okw_…`.",
      "Quit Claude Desktop fully and reopen it. The **tools** control under the message box lists the server.",
    ],
    snippetLabel: "claude_desktop_config.json",
    snippet: claudeDesktopConfig,
    pitfalls: [
      "Windows: Claude Desktop can't invoke `npx` (the unquoted path to `npx.cmd` fails, and one crashing server takes every other entry down). Run `npm install -g mcp-remote`, set `command` to `%APPDATA%\\npm\\mcp-remote.cmd`, and drop `\"mcp-remote\"` from `args`.",
      "`--header` must be exact `Name:Value`. A space after the colon is dropped silently and auth fails, with only a warning line in the logs.",
      "A Store install of Claude Desktop keeps the live config at `%LOCALAPPDATA%\\Packages\\Claude_<id>\\LocalCache\\Roaming\\Claude\\claude_desktop_config.json`, not under `%APPDATA%\\Claude\\`. Save it without a byte-order mark.",
      "Claude's **Connectors** (claude.ai, and Settings → Connectors in the app) need OAuth, which the hosted server doesn't offer yet. mcp-remote is the path.",
    ],
    docs: { label: "mcp-remote", href: "https://www.npmjs.com/package/mcp-remote" },
  },
  {
    id: "any",
    label: "Any MCP client",
    via: "the URL and the header",
    needs: "A client that speaks MCP over Streamable HTTP and can send a header.",
    steps: [
      "Point the client at the URL over **Streamable HTTP**.",
      "Send the key as `Authorization: Bearer okw_…`, or as `x-okwan-key: okw_…` where a client can't set that header. VS Code reads the same URL and header from `.vscode/mcp.json` under `servers`, with `\"type\": \"http\"`.",
      "Clients that only do OAuth (ChatGPT connectors, claude.ai Connectors) can't connect yet: the hosted server takes an API key only.",
    ],
    snippetLabel: "Connection facts",
    snippet: (apiBase) =>
      [
        "Transport  Streamable HTTP",
        `URL        ${mcpUrl(apiBase)}`,
        "Header     Authorization: Bearer okw_…",
        "           or x-okwan-key: okw_…",
        "Auth       API key only; no OAuth",
        `Tools      ${HOSTED_TOOL_NAMES.slice(0, 2).join(", ")},`,
        `           ${HOSTED_TOOL_NAMES.slice(2).join(", ")} (all read-only)`,
      ].join("\n"),
    docs: { label: "MCP: connecting a client", href: "https://modelcontextprotocol.io/quickstart/user" },
  },
];

export type McpClientId = (typeof MCP_CLIENTS)[number]["id"];

export function mcpClientOf(value: string | null | undefined): McpClientId {
  return MCP_CLIENTS.find((c) => c.id === value)?.id ?? MCP_CLIENTS[0].id;
}
