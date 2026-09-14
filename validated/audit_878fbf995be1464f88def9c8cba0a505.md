Based on my research, I found a legitimate SSRF analog in the Chia DataLayer subsystem.

### Title
Unvalidated Mirror/Subscription URLs Enable SSRF in DataLayer File Download - (File: chia/data_layer/download_data.py)

### Summary
Chia's DataLayer periodically fetches `.dat` delta/full-tree files from server URLs sourced from user-supplied subscription input and on-chain mirror coin data, with no validation that the target host is a public, non-internal address. This mirrors the Pydantic AI SSRF pattern: an attacker-controlled URL is fed into a server-side HTTP fetch without SSRF protections (private-IP blocking, cloud metadata blocking, redirect validation).

### Finding Description
`DataLayerRpcApi.subscribe()` accepts arbitrary `urls` from the RPC caller and stores them unmodified via `DataLayer.subscribe()`: ` [1](#0-0) ` ` [2](#0-1) `. These URLs, and mirror URLs discovered from the on-chain DataLayer mirror coins (which any wallet/store owner can publish via `add_mirror`), are persisted in `DataStore` and later retrieved by `fetch_and_validate()`: ` [3](#0-2) `.

Those URLs are passed straight into `http_download()`, which performs an `aiohttp` GET against `server_info.url + "/" + filename` with no scheme restriction, no DNS-rebinding protection, no private-IP/loopback/link-local blocking, and no cloud-metadata blocklist: ` [4](#0-3) `. This is functionally identical to the vulnerable `download_item()` pattern described in the Pydantic AI advisory — a server-side component performs an HTTP fetch to a URL supplied (directly or indirectly, via subscribe or on-chain mirror publication) by an untrusted party, with no SSRF hardening.

The static file server side (`DataLayerServer.file_handler`/`folder_handler`) only validates filenames, not the origin URL used to fetch from a mirror, confirming that filename validation was never intended as an SSRF control: ` [5](#0-4) `.

### Impact Explanation
Any DataLayer participant — a remote party publishing a mirror URL for a store the victim subscribes to, or a local/RPC caller invoking `subscribe` with a crafted URL — can cause the victim's `DataLayer` service to issue outbound HTTP requests to `http://127.0.0.1/...`, `http://169.254.169.254/...` (cloud metadata), or other internal/private network addresses on a periodic basis (`periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()`). This can be used to probe/exfiltrate internal services or cloud instance credentials reachable from the node's network, and can also be used to trigger repeated internal-network requests (internal port scanning). This does not directly cause unauthorized coin movement or consensus divergence, so it is scoped as an availability/confidentiality issue against the operator's environment rather than a chain-state integrity bug.

### Likelihood Explanation
Likelihood is high for any operator running DataLayer with subscriptions to externally-controlled stores or public mirrors: mirror URLs are attacker-influenced by design (any store owner can call `add_mirror` with an arbitrary URL, and `subscribe` accepts free-form `urls`), and `fetch_and_validate()` runs automatically in the background sync loop without operator confirmation per URL.

### Recommendation
Add SSRF protections to `http_download()` in `chia/data_layer/download_data.py` and to the URL acceptance paths (`DataLayer.subscribe()`, mirror URL ingestion in `data_layer_wallet.py`): restrict schemes to `http`/`https`, resolve the hostname before connecting and reject loopback/private/link-local/CGNAT/unique-local ranges and known cloud metadata addresses (`169.254.169.254`, etc.) unless explicitly allow-listed by the operator, and validate each HTTP redirect target against the same rules.

### Proof of Concept
1. Store owner A publishes a DataLayer mirror coin for `store_id` with URL `http://169.254.169.254/latest/meta-data/iam/security-credentials/` via `add_mirror`.
2. Victim node subscribes to `store_id` (e.g., via `dl subscribe` or normal replication interest).
3. During the next `periodically_manage_data()` cycle, `fetch_and_validate()` selects that mirror's `ServerInfo` and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which issues an `aiohttp` GET directly to the metadata endpoint URL with no host validation: ` [6](#0-5) `.
4. The victim's node makes an outbound request to the internal/cloud-metadata address, demonstrating SSRF.

### Citations

**File:** chia/data_layer/data_layer_rpc_api.py (L360-371)
```python
    async def subscribe(self, request: dict[str, Any]) -> EndpointResult:
        """
        subscribe to singleton
        """
        store_id = request.get("id")
        if store_id is None:
            raise Exception("missing store id in request")

        store_id_bytes = bytes32.from_hexstr(store_id)
        urls = request.get("urls", [])
        await self.service.subscribe(store_id=store_id_bytes, urls=urls)
        return {}
```

**File:** chia/data_layer/data_layer.py (L642-666)
```python
        timestamp = int(time.time())
        servers_info = await self.data_store.get_available_servers_for_store(store_id, timestamp)
        # TODO: maybe append a random object to the whole DataLayer class?
        random.shuffle(servers_info)
        success = False
        for server_info in servers_info:
            url = server_info.url

            root = await self.data_store.get_tree_root(store_id=store_id)
            if root.generation > singleton_record.generation:
                self.log.info(
                    "Fetch data: local DL store is ahead of chain generation. "
                    f"Local root: {root}. Singleton: {singleton_record}"
                )
                break
            if root.generation == singleton_record.generation:
                self.log.info(f"Fetch data: wallet generation matching on-chain generation: {store_id}.")
                break

            self.log.info(
                f"Downloading files {store_id}. "
                f"Current wallet generation: {root.generation}. "
                f"Target wallet generation: {singleton_record.generation}. "
                f"Server used: {url}."
            )
```

**File:** chia/data_layer/data_layer.py (L895-902)
```python
    async def subscribe(self, store_id: bytes32, urls: list[str]) -> Subscription:
        parsed_urls = [url.rstrip("/") for url in urls]
        subscription = Subscription(store_id, [ServerInfo(url, 0, 0) for url in parsed_urls])
        await self.wallet_rpc.dl_track_new(DLTrackNew(launcher_id=subscription.store_id))
        async with self.subscription_lock:
            await self.data_store.subscribe(subscription)
        self.log.info(f"Done adding subscription: {subscription.store_id}")
        return subscription
```

**File:** chia/data_layer/download_data.py (L298-324)
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
