## Title
DataLayer mirror URLs trigger unauthenticated outbound HTTP requests (SSRF) to attacker-chosen hosts - ([File: chia/data_layer/data_layer.py])

### Summary
DataLayer treats mirror/subscription URLs as untrusted "external trust inputs" per the module's own documentation, yet the periodic sync loop (`fetch_and_validate` → `insert_from_delta_file` → `download_file` → `http_download`) makes the node's own outbound HTTP client fetch attacker-controlled URLs with no host/scheme allow-listing, and `get_downloader()` similarly POSTs to arbitrary plugin URLs. This is the same bug class as the referenced Audiobookshelf CVE-2023-47619: a semi-privileged actor supplies a URL, and the server-side process performs the outbound request and reads/writes the response into local storage.

### Finding Description
`DataLayer.fetch_and_validate()` iterates `servers_info` (URLs associated with a subscribed store) and calls `insert_from_delta_file(...)`, which calls `download_file()`, which — when no downloader plugin handles the request — calls `http_download()`: [1](#0-0) 
This performs an `aiohttp` GET to `server_info.url + "/" + filename` with no restriction on scheme, host, or address (no blocking of `localhost`, link-local, or internal RFC1918 ranges), i.e., a classic SSRF sink: an attacker who can register/point a mirror URL for a store the node subscribes to can make the node's process issue GET requests to arbitrary internal or external endpoints, and the response body is written to disk under the node's `client_foldername`. [2](#0-1) 

Separately, `DataLayer.get_downloader()` POSTs a JSON body containing the store id and the *subscription-provided URL* to every configured downloader plugin's `/handle_download` endpoint: [3](#0-2) 
The module's own internal documentation states explicitly that "Plugin and mirror URLs are external trust inputs" and that downloaded data must be re-verified, but the verification (merkle-root reconstruction) only proves file *content* integrity — it does nothing to prevent the SSRF request itself from being made to internal services (metadata endpoints, other local RPC ports, internal LAN hosts), which is exactly the request/response leak pattern in the Audiobookshelf report (arbitrary GET to attacker URL, response processed by the server). [4](#0-3) 

The static `DataLayerServer.file_handler`/`folder_handler` were also reviewed as a potential arbitrary-file-read analog, but both are guarded by `is_filename_valid()` before path construction, constraining the reachable path to hash-derived filenames within `server_dir`, so no path-traversal / arbitrary-read primitive was found there. [5](#0-4) 

### Impact Explanation
A Data Layer client/operator with only the ability to add a subscription/mirror URL for a store (a much lower privilege than modifying chain state) can cause the DataLayer service process to issue outbound HTTP requests to arbitrary hosts and ports reachable from the node's network position, and can direct plugin downloaders to POST attacker-influenced data to arbitrary URLs. This can be used to probe/attack internal infrastructure (cloud metadata services, other local RPC ports, internal-only HTTP services) from the node's trusted network vantage point — the core SSRF impact class described in CVE-2023-47619 (confidentiality impact via response disclosure, some availability/pivot risk). It does not directly yield unauthorized coin movement or consensus divergence, so it sits at the boundary of the requested severity categories, but it matches the "SSRF / arbitrary outbound fetch controlled by a semi-trusted local actor" bug class from the report.

### Likelihood Explanation
Reaching this requires being able to add a mirror/subscription entry for a store, which is a legitimate, low-privilege DataLayer RPC/CLI operation intended for normal mirror configuration (`get_available_servers_for_store` populates `servers_info` from subscription table entries, and `fetch_and_validate` runs it automatically on the periodic sync loop). No additional signing or chain confirmation is required to add a subscription URL before it is fetched. [6](#0-5) 

### Recommendation
Add SSRF hardening to `http_download()` and `get_downloader()`: resolve and validate the target host against a deny-list of private/loopback/link-local ranges (or an explicit allow-list) before issuing the request, and disallow non-HTTP(S) redirects/schemes. Apply the same validation to plugin `downloader.url` and to `s3_plugin_service.py`'s `download()` handler, which also trusts client-supplied `url`/`filename` fields.

### Proof of Concept
1. As a Data Layer client, subscribe to a store and register/advertise a mirror URL pointing at an internal-only address (e.g. `http://169.254.169.254/latest/meta-data/` or `http://127.0.0.1:<internal-port>/`).
2. Wait for (or trigger) `periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()` to run.
3. Observe that `http_download()` in `chia/data_layer/download_data.py` performs a GET to the attacker-chosen URL from the node's process/network context; the response is written to `target_filename_path` and, if it happens to parse as a delta file, is fed into `data_store.insert_into_data_store_from_file()` — otherwise the raw bytes are still fetched and available for size/timing/error-based information disclosure about the internal target. [7](#0-6)

### Citations

**File:** chia/data_layer/download_data.py (L110-145)
```python
async def download_file(
    data_store: DataStore,
    target_filename_path: Path,
    store_id: bytes32,
    root_hash: bytes32,
    generation: int,
    server_info: ServerInfo,
    proxy_url: str | None,
    downloader: PluginRemote | None,
    timeout: aiohttp.ClientTimeout,
    client_foldername: Path,
    timestamp: int,
    log: logging.Logger,
    grouped_by_store: bool,
    group_downloaded_files_by_store: bool,
    max_delta_file_size: int,
) -> bool:
    if target_filename_path.exists():
        return True
    filename = get_delta_filename(store_id, root_hash, generation, grouped_by_store)

    if downloader is None:
        # use http downloader - this raises on any error
        try:
            await http_download(
                target_filename_path, filename, proxy_url, server_info, timeout, log, max_delta_file_size
            )
        except (asyncio.TimeoutError, aiohttp.ClientError, MaxDeltaFileSizeExceededError):
            new_server_info = await data_store.server_misses_file(store_id, server_info, timestamp)
            log.info(
                f"Failed to download {filename} from {new_server_info.url}."
                f"Miss {new_server_info.num_consecutive_failures}."
            )
            log.info(f"Next attempt from {new_server_info.url} in {new_server_info.ignore_till - timestamp}s.")
            return False
        return True
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

**File:** chia/data_layer/data_layer.py (L731-747)
```python
    async def get_downloader(self, store_id: bytes32, url: str) -> PluginRemote | None:
        request_json = {"store_id": store_id.hex(), "url": url}
        for d in self.downloaders:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(
                        d.url + "/handle_download",
                        json=request_json,
                        headers=d.headers,
                        timeout=self.client_timeout,
                    ) as response:
                        res_json = await response.json()
                        if res_json["handle_download"]:
                            return d
                except Exception as e:
                    self.log.error(f"get_downloader could not get response: {type(e).__name__}: {e}")
        return None
```

**File:** .cursor/context/data-layer.md (L87-89)
```markdown
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
- DataLayer wallet code depends on singleton CLVM structure, odd singleton amounts, lineage proofs, and offer solver field names. Changes in wallet puzzle drivers or offer summaries can break this module without direct edits here.
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
