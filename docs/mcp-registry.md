# MCP registry metadata

**What this is:** the draft `server.json` manifest for the official MCP registry.
**For:** anyone checking whether the gateway is discoverable. **Status:** not published.

The file is schema-valid metadata, but the package entry is a placeholder and the server is not in the registry. The CLI and the manifest are not packaged yet, so a registry client could not launch it. Three things are missing:

1. **A runnable package.** The manifest points at the PyPI name `forge-agent-mcp`. The
   package is not on PyPI yet, has no console entry point, and does not ship the corpus
   manifest. Treat the package block as a placeholder until that packaging work is done.
2. **A published package and ownership mark.** Publishing needs an
   `mcp-name: io.github.ArpanSM/forge-agent-mcp` line in the package README so the registry can
   verify ownership.
3. **A public endpoint.** The demo runs on `127.0.0.1`. A remote entry needs a public HTTPS
   URL and a deployment, which this repository does not have.

When those exist, the publish flow is:

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
