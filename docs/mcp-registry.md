# MCP registry metadata

**What this is:** the draft `server.json` manifest for the official MCP registry.
**For:** anyone checking whether the gateway is discoverable. **Status:** not published.

The file is valid metadata, but the server is not in the registry. Two things are missing:

1. **A published package.** The manifest points at the PyPI name `forge-agent-mcp`. The
   package is not on PyPI yet. Publishing it needs an `mcp-name: io.github.ArpanSM/forge-agent-mcp`
   line in the package README so the registry can verify ownership.
2. **A public endpoint.** The demo runs on `127.0.0.1`. A remote entry needs a public HTTPS
   URL and a deployment, which this repository does not have.

When both exist, the publish flow is:

```bash
mcp-publisher login github
mcp-publisher validate
mcp-publisher publish
```

The registry only stores metadata. It does not host the server or run the tools.

## Why ship a draft at all

Because the shape is part of the design. A reviewer can see the intended name, transport and
package type without mistaking the project for a published service. The draft is also a useful
checklist for the production work in [production-path.md](production-path.md).
