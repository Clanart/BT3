### Title
Unauthenticated Public Access to DataLayer Store Files via `DataLayerServer` HTTP Endpoint - (File: `chia/data_layer/data_layer_server.py`)

### Summary
`DataLayerServer`, the process that serves Chia DataLayer `.dat` (root/delta/full-tree Merkle) files over HTTP, listens by default on `0.0.0.0` with no TLS and no authentication/authorization check on its file-serving routes. Any network client that can reach the configured `host_port` can download the complete Merkle blob / key-value data for every store the node serves, mirroring the OAI-PMH advisory's "unauthenticated public access to all media and metadata by default" bug class (CWE-862, missing authorization on a default-enabled data-serving endpoint).

### Finding Description
`DataLayerServer.start()` binds a plain `aiohttp` `WebServer` with two `GET` routes and no SSL context, no auth middleware, and no peer/role check: [1](#0-0) 

Both handlers only validate that the filename shape is well-formed via `is_filename_valid()`; they do not check the caller's identity, IP allowlist, or any credential before returning file contents: [2](#0-1) 

The default configuration binds this server to all interfaces: [3](#0-2) 

Unlike the local admin RPC (`chia/rpc/rpc_server.py`) or the daemon websocket (`chia/daemon/server.py`), both of which require mutual TLS with private CA certs as described in `.cursor/context/ssl.md` and `.cursor/context/rpc.md`, the `DataLayerServer` file endpoints intentionally run outside that trust boundary — it is meant to be the public mirror/sync endpoint other DataLayer peers pull files from. However, this means any store operator who runs DataLayer with defaults (host bound to `0.0.0.0`) exposes the entire committed dataset (all keys/values reconstructable from the Merkle blob/delta files) to any unauthenticated network client, exactly matching the OAI-PMH analog: a default-enabled, network-reachable data publication surface with no authorization gate, requiring active operator intervention (firewalling, reverse-proxy auth, etc.) to protect data that users may not realize is public.

### Impact Explanation
DataLayer is frequently used to store off-chain key/value application data (potentially business-sensitive or user data) anchored by an on-chain singleton. Because the HTTP file server has no authorization check and defaults to binding all interfaces, an unauthenticated remote attacker can enumerate and download full/delta tree files and reconstruct the complete key/value dataset for every store the node participates in, causing confidentiality loss (Impact: Confidentiality High per the analogous CVSS vector, similar to the referenced advisory `AV:N/AC:L/PR:L/UI:R/S:C/C:H/I:L/A:N`). This does not corrupt consensus or allow coin theft, but it is a direct data-confidentiality violation reachable by any Data Layer client/network peer without credentials, consistent with the "Data Layer roots and proofs" reachable class in scope.

### Likelihood Explanation
Likelihood is high for any operator who runs `data_layer` with default config and exposes the port to the network (which is required for legitimate mirror/subscription functionality) — there is no code path requiring the operator to opt into authorization; the exposure is "on by default" the same way OAI-PMH was on by default in Opencast. Filename validation prevents path traversal but does nothing to restrict who may fetch valid store files.

### Recommendation
Add an authorization/allowlist layer to `DataLayerServer.file_handler`/`folder_handler` (e.g., require API keys, mutual TLS with a private CA similar to other Chia local services, or explicit IP allowlisting) and change the default `host_ip` to `127.0.0.1` unless the operator explicitly opts into public mirroring, similarly to how the OAI-PMH patch changed the default required role from `ROLE_ANONYMOUS` to `ROLE_ADMIN`.

### Proof of Concept
1. Start `chia data_layer_http` (or the packaged `data_layer_http` service) with default config (`host_ip: 0.0.0.0`, `host_port: 8575`).
2. From any remote host with network access to that port, send `GET http://<node-ip>:8575/<known-valid-filename>` or `GET http://<node-ip>:8575/<tree_id>/<filename>` with a filename that satisfies `is_filename_valid()` (filenames are derived deterministically from store id/root hash and are discoverable, e.g., via wallet singleton root history or subscription metadata).
3. The server returns the raw `.dat` file contents with `200 OK` and no authentication challenge, allowing full reconstruction of the store's key/value data without any credentials.

### Citations

**File:** chia/data_layer/data_layer_server.py (L64-71)
```python
        self.webserver = await WebServer.create(
            hostname=self.host_ip,
            port=self.port,
            routes=[
                web.get("/{filename}", self.file_handler),
                web.get("/{tree_id}/{filename}", self.folder_handler),
            ],
        )
```

**File:** chia/data_layer/data_layer_server.py (L91-118)
```python
    async def file_handler(self, request: web.Request) -> web.Response:
        filename = request.match_info["filename"]
        if not is_filename_valid(filename):
            raise Exception("Invalid file format requested.")
        file_path = self.server_dir.joinpath(filename)
        with open(file_path, "rb") as reader:
            content = reader.read()
        response = web.Response(
            content_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment;filename={filename}"},
            body=content,
        )
        return response

    async def folder_handler(self, request: web.Request) -> web.Response:
        tree_id = request.match_info["tree_id"]
        filename = request.match_info["filename"]
        if not is_filename_valid(tree_id + "-" + filename):
            raise Exception("Invalid file format requested.")
        file_path = self.server_dir.joinpath(tree_id).joinpath(filename)
        with open(file_path, "rb") as reader:
            content = reader.read()
        response = web.Response(
            content_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment;filename={filename}"},
            body=content,
        )
        return response
```

**File:** chia/util/initial-config.yaml (L693-696)
```yaml
  # Data for running a data layer server.
  host_ip: 0.0.0.0
  host_port: 8575
  # Data for running a data layer client.
```
