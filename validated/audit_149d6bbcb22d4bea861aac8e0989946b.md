## #Vulnerability found for this question.

### Title
Data Layer `add_missing_files` RPC endpoint allows unauthenticated-directory-scope local RPC caller to force unbounded writes of large tree files to an arbitrary filesystem path, causing disk-exhaustion DoS - (File: `chia/data_layer/data_layer_rpc_api.py`)

### Summary
The Data Layer RPC route `/add_missing_files` accepts an arbitrary, attacker-controlled `foldername` string from the JSON request body and passes it, unvalidated, all the way down to the low-level file writer that regenerates every historical full-tree and delta ("diff") file for a store. This is directly analogous to the H2O `run_tool`/`XGBoostLibExtractTool` bug class: a generic-purpose RPC command exposes an internal file-writing capability with no bound on target directory or on the volume/size of data written, letting a caller of the RPC surface write very large files into any directory the service process can reach.

### Finding Description
The `DataLayerRpcApi.get_routes()` table registers `/add_missing_files` as a normal endpoint reachable by any client that can talk to the Data Layer RPC port. [1](#0-0) 

The handler reads `foldername` straight from the request dict with no allow-list, no restriction to `server_files_location`, and no path-traversal or absolute-path check: [2](#0-1) 

That path is forwarded into `DataLayer.add_missing_files()`, which loops over *every generation from 1 to `max_generation`* of the store's history and calls `write_files_for_root()` once per generation for the caller-chosen directory: [3](#0-2) 

`write_files_for_root()` creates parent directories as needed (`mkdir(parents=True, exist_ok=True)`) under the caller-supplied `foldername` and writes both a full-tree file and a delta file by serializing the entire Merkle tree for that generation via `data_store.write_tree_to_file()`: [4](#0-3) 

Unlike the peer-facing download path (`http_download`), which enforces `max_delta_file_size`, this local-write path has no size cap at all — it will fully serialize and write out the entire tree for every generation that exists. [5](#0-4) 

The Data Layer RPC boundary is documented as a semi-trusted local surface (TLS-protected but not fine-grained-permissioned), and Data Layer clients/local RPC callers are within the accepted reach for this analysis. [6](#0-5) 

### Impact Explanation
A local/unprivileged Data Layer RPC caller (or any local process with access to the RPC cert/port, which is a broader trust boundary than a wallet-holder-only operation) can invoke `add_missing_files` with:
- `foldername` pointed at any directory writable by the Data Layer process (e.g. the root filesystem, a shared volume, or another service's data directory), and
- a store with many generations and/or large trees,

causing the service to synchronously write two full-serialization files per generation with no size limit, for every generation of every store (or all owned stores if `ids` is omitted). This can exhaust disk space on arbitrary paths reachable by the process (denial of service against the node/wallet/Data Layer host) and can pollute/overwrite files in directories outside the intended `server_files_location` sandbox (unauthorized file write), directly matching the CWE-400 (DoS) / CWE-94-adjacent "write large files to arbitrary directories" pattern described in the H2O advisory.

### Likelihood Explanation
No authentication beyond the standard Data Layer RPC TLS access is required, and no additional authorization/ownership check gates the `foldername` parameter (contrast with `batch_insert()`, which does check DataLayer-wallet ownership before mutating). Any client capable of reaching the Data Layer RPC endpoint can trigger this with a single call, making likelihood high once RPC access is available.

### Recommendation
- Reject or ignore attacker-supplied `foldername` values that escape the configured `server_files_location` (canonicalize and enforce prefix containment, disallow absolute paths / `..` components).
- Add write-size/iteration bounds (mirroring `max_delta_file_size` used in the download path) to `write_files_for_root()`/`add_missing_files()`, and cap how many generations/stores can be regenerated per call.
- Consider requiring explicit administrative confirmation or an additional permission check before allowing a custom write directory at all.

### Proof of Concept
1. Start `chia_data_layer` with at least one store containing a non-trivial history (several generations, moderate-size KV data).
2. Call the RPC:
```
POST /add_missing_files
{
  "foldername": "/tmp/attacker_dir"   # or any path writable by the daemon
}
```
3. Observe `DataLayer.add_missing_files()` iterating every generation from 1 to `max_generation` and `write_files_for_root()` writing a full-tree file and a diff file per generation into `/tmp/attacker_dir`, with total bytes written proportional to `(tree size) × (generation count)` and no size ceiling — repeated/large-store calls can fill the target filesystem.

### Citations

**File:** chia/data_layer/data_layer_rpc_api.py (L126-128)
```python
            "/get_kv_diff": self.get_kv_diff,
            "/get_root_history": self.get_root_history,
            "/add_missing_files": self.add_missing_files,
```

**File:** chia/data_layer/data_layer_rpc_api.py (L401-417)
```python
    async def add_missing_files(self, request: dict[str, Any]) -> EndpointResult:
        """
        complete the data server files.
        """
        if "ids" in request:
            store_ids = request["ids"]
            ids_bytes = [bytes32.from_hexstr(id) for id in store_ids]
        else:
            subscriptions: list[Subscription] = await self.service.get_subscriptions()
            ids_bytes = [subscription.store_id for subscription in subscriptions]
        overwrite = request.get("overwrite", False)
        foldername: Path | None = None
        if "foldername" in request:
            foldername = Path(request["foldername"])
        for store_id in ids_bytes:
            await self.service.add_missing_files(store_id, overwrite, foldername)
        return {}
```

**File:** chia/data_layer/data_layer.py (L843-869)
```python
    async def add_missing_files(self, store_id: bytes32, overwrite: bool, foldername: Path | None) -> None:
        root = await self.data_store.get_tree_root(store_id=store_id)
        latest_generation = root.generation
        full_tree_first_publish_generation = max(0, latest_generation - self.maximum_full_file_count + 1)
        singleton_record = (
            await self.wallet_rpc.dl_latest_singleton(DLLatestSingleton(launcher_id=store_id, only_confirmed=True))
        ).singleton
        if singleton_record is None:
            self.log.error(f"No singleton record found for: {store_id}")
            return
        max_generation = min(singleton_record.generation, root.generation)
        server_files_location = foldername if foldername is not None else self.server_files_location
        files = []
        for generation in range(1, max_generation + 1):
            root = await self.data_store.get_tree_root(store_id=store_id, generation=generation)
            res = await write_files_for_root(
                self.data_store,
                store_id,
                root,
                server_files_location,
                full_tree_first_publish_generation,
                overwrite,
                self.group_files_by_store,
            )
            files.append(res.diff_tree.name)
            if res.full_tree is not None:
                files.append(res.full_tree.name)
```

**File:** chia/data_layer/download_data.py (L69-107)
```python
async def write_files_for_root(
    data_store: DataStore,
    store_id: bytes32,
    root: Root,
    foldername: Path,
    full_tree_first_publish_generation: int,
    overwrite: bool = False,
    group_by_store: bool = False,
) -> WriteFilesResult:
    if root.node_hash is not None:
        node_hash = root.node_hash
    else:
        node_hash = bytes32.zeros  # todo change

    filename_full_tree = get_full_tree_filename_path(foldername, store_id, node_hash, root.generation, group_by_store)
    filename_diff_tree = get_delta_filename_path(foldername, store_id, node_hash, root.generation, group_by_store)
    filename_full_tree.parent.mkdir(parents=True, exist_ok=True)

    written = False
    mode: Literal["wb", "xb"] = "wb" if overwrite else "xb"

    written_full_file = False
    if root.generation >= full_tree_first_publish_generation:
        try:
            with open(filename_full_tree, mode) as writer:
                await data_store.write_tree_to_file(root, node_hash, store_id, False, writer)
            written = True
            written_full_file = True
        except FileExistsError:
            pass

    try:
        with open(filename_diff_tree, mode) as writer:
            await data_store.write_tree_to_file(root, node_hash, store_id, True, writer)
        written = True
    except FileExistsError:
        pass

    return WriteFilesResult(written, filename_full_tree if written_full_file else None, filename_diff_tree)
```

**File:** chia/data_layer/download_data.py (L298-345)
```python
async def http_download(
    target_filename_path: Path,
    filename: str,
    proxy_url: str | None,
    server_info: ServerInfo,
    timeout: aiohttp.ClientTimeout,
    log: logging.Logger,
    max_delta_file_size: int,
) -> None:
    """
    Download a file from a server using aiohttp.
    Raises exceptions on errors
    """
    async with aiohttp.ClientSession() as session:
        headers = {"accept-encoding": "gzip"}
        async with session.get(
            server_info.url + "/" + filename,
            headers=headers,
            timeout=timeout,
            proxy=proxy_url,
        ) as resp:
            resp.raise_for_status()
            size = int(resp.headers.get("content-length", 0))
            max_delta_file_size_bytes = max_delta_file_size * 1024 * 1024
            if size > max_delta_file_size_bytes:
                raise MaxDeltaFileSizeExceededError(f"Maximum delta file size exceeded: {max_delta_file_size} MiB.")
            log.debug(f"Downloading delta file {filename}. Size {size} bytes.")
            progress_byte = 0
            progress_percentage = f"{0:.0%}"
            success = False
            try:
                with target_filename_path.open(mode="wb") as f:
                    async for chunk, _ in resp.content.iter_chunks():
                        f.write(chunk)
                        progress_byte += len(chunk)
                        if progress_byte > max_delta_file_size_bytes:
                            raise MaxDeltaFileSizeExceededError(
                                f"Maximum delta file size exceeded: {max_delta_file_size} MiB."
                            )
                        if size > 0:
                            new_percentage = f"{progress_byte / size:.0%}"
                            if new_percentage != progress_percentage:
                                progress_percentage = new_percentage
                                log.info(f"Downloading delta file {filename}. {progress_percentage} of {size} bytes.")
                success = True
            finally:
                if not success:
                    target_filename_path.unlink(missing_ok=True)
```

**File:** .cursor/context/rpc.md (L80-86)
```markdown
## Fragility Hotspots

- Highest-risk edits: changing automatic `"success"` insertion, changing common route names, broadening `RpcClient.fetch()` failure behavior, altering daemon websocket command routing, or making RPC lifecycle assumptions about peer-server availability.
- Error compatibility is easy to break: HTTP includes traceback in failure responses, websocket failures do not, and `ResponseFailureError` preserves the full JSON body.
- The RPC boundary is semi-trusted local/admin surface protected by private TLS in normal service mode, not an untrusted P2P path. Do not move peer protocol rate-limiting or node-type authorization assumptions into this layer.
- Route discovery returns every shared and service-specific route. Adding sensitive operational endpoints should be evaluated as local admin API exposure even when not reachable through the P2P protocol.
- `marshal()` depends on runtime type hints. Missing or future-deferred annotations that cannot resolve will fail at decoration/call setup rather than inside endpoint business logic.
```
