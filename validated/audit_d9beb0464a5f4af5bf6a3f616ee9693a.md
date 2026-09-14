## Finding: Unauthenticated Data Layer file server blocks the asyncio event loop on synchronous full-file reads

### Title
Unauthenticated Data Layer HTTP file server performs synchronous blocking file reads on the asyncio event loop, enabling event-loop starvation DoS - (File: chia/data_layer/data_layer_server.py)

### Summary
`DataLayerServer` starts a plain (non-TLS, unauthenticated) `WebServer` whose only two routes serve arbitrary local delta/full-tree files directly to any caller. Both handlers perform a synchronous, unbounded `open(...).read()` call inline inside an `async def` coroutine, which blocks the single-threaded asyncio event loop for the full duration of the disk read and while the entire file is materialized in memory.

### Finding Description
`DataLayerServer.start()` builds its `WebServer` without ever passing an `ssl_context`, so the file-serving HTTP listener runs in plain HTTP with no authentication of any kind: [1](#0-0) 

The two exposed routes, `file_handler` and `folder_handler`, only validate the *filename shape* via `is_filename_valid()`, then perform a fully synchronous read of the requested file directly in the coroutine body: [2](#0-1) 

`is_filename_valid()` only checks that the filename round-trips through the store-id/node-hash/generation encoding — it does not bound file size or rate-limit requests: [3](#0-2) 

Because `open(file_path, "rb").read()` is a blocking syscall executed synchronously inside the coroutine (never offloaded via `run_in_executor` or streamed in chunks), the entire aiohttp event loop is stalled for the time it takes to read the file from disk into memory. Full-tree/delta files can be large — `maximum_full_file_count` and `max_delta_file_size` bound retention/parsing on the *client* ingestion side, but nothing bounds how large a file the *server* can be asked to serve in one synchronous read. This module context confirms the design gap: "Static file serving validates filenames but uses direct synchronous file reads in the request handler," which is treated as an assumption rather than a hardened guarantee: [4](#0-3) 

Any unauthenticated Data Layer HTTP client (mirror/plugin consumer, or any network peer able to reach the configured `host_ip`/`host_port`) can repeatedly request the largest known full-tree files. Each such request occupies the single event loop thread synchronously; concurrent requests for large files serialize and starve all other coroutines on that process (including legitimate mirror sync work), and repeated concurrent requests can also drive memory usage up because each handler buffers the complete file contents in `content` before responding.

### Impact Explanation
This is a Data-Layer-client-reachable, unauthenticated Denial of Service: a remote, unauthenticated caller can stall the Data Layer HTTP service's single event loop and degrade/deny legitimate file serving and background sync work for arbitrarily long periods by repeatedly requesting large stored full/delta files. This matches the reachable class named in scope ("Data Layer roots and proofs" / "Data Layer client") and results in a spend/service-processing halt for the Data Layer service, analogous to the Flowise `get-upload-file` DoS where unauthenticated file-serving input handling caused a full-instance stall/crash.

### Likelihood Explanation
High likelihood: the endpoint requires no authentication, no TLS client cert, and no special knowledge beyond a valid store-id/node-hash/generation filename that is discoverable from any store's known root history (these values are meant to be publicly shared for data layer mirroring). No CLVM cost limits or mempool gating apply since this is a bare HTTP file server, not a spend-bundle path.

### Recommendation
- Serve files using non-blocking I/O (e.g., `aiofiles`, `loop.run_in_executor`, or streaming `web.FileResponse`/chunked `StreamResponse`) so a single large read cannot block the event loop.
- Enforce a maximum file size / rate limit per client on `file_handler`/`folder_handler` before/while reading.
- Consider requiring TLS/allow-listing for this server, consistent with other Chia RPC/service surfaces, rather than exposing it as a bare unauthenticated HTTP listener.

### Proof of Concept
1. Start a `data_layer_http` service so `DataLayerServer.start()` binds its plain-HTTP `WebServer` (no `ssl_context` passed) per [1](#0-0) .
2. Identify (or wait for) a store with a large full-tree/delta file under `server_files_location` (filenames are protocol-defined and discoverable via chain-published roots).
3. From an unauthenticated client, issue many concurrent `GET /{filename}` (or `GET /{tree_id}/{filename}`) requests for that large file.
4. Observe that each request triggers a synchronous `open().read()` in `file_handler`/`folder_handler` [5](#0-4) , serializing on the single event loop thread and stalling all other in-flight requests/background sync coroutines on the same process for the duration of the concurrent reads.

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

**File:** chia/data_layer/download_data.py (L28-60)
```python
def is_filename_valid(filename: str, group_by_store: bool = False) -> bool:
    if group_by_store:
        if filename.count("/") != 1:
            return False
        filename = filename.replace("/", "-")

    split = filename.split("-")

    try:
        raw_store_id, raw_node_hash, file_type, raw_generation, raw_version, *rest = split
        store_id = bytes32(bytes.fromhex(raw_store_id))
        node_hash = bytes32(bytes.fromhex(raw_node_hash))
        generation = int(raw_generation)
    except ValueError:
        return False

    if len(rest) > 0:
        return False

    # TODO: versions should probably be centrally defined
    if raw_version != "v1.0.dat":
        return False

    if file_type not in {"delta", "full"}:
        return False

    generate_file_func = get_delta_filename if file_type == "delta" else get_full_tree_filename
    reformatted = generate_file_func(
        store_id=store_id, node_hash=node_hash, generation=generation, group_by_store=False
    )

    return reformatted == filename

```

**File:** .cursor/context/data-layer.md (L81-81)
```markdown
- Static file serving validates filenames but uses direct synchronous file reads in the request handler. It assumes files are already written under `server_files_location` by the service or plugin.
```
