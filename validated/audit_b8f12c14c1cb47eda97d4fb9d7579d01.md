### Title
SSRF in DataLayer subscribe/fetch — arbitrary attacker-supplied mirror URLs fetched by the node's HTTP client - ([File: chia/data_layer/download_data.py])

### Summary
Chia's DataLayer service maintains "subscriptions" that record arbitrary `urls` for a given `store_id` and periodically fetches data from those URLs via server-side HTTP requests, analogous to the Superset "import dataset" SSRF: an authorized local caller supplies a URL that is not validated against internal/private network ranges before the server issues a request to it.

### Finding Description
`DataLayer.subscribe()` accepts a caller-supplied list of `urls`, only doing `url.rstrip("/")`, and persists them as `ServerInfo(url, 0, 0)` records without any scheme/host/IP validation: [1](#0-0) 

These persisted URLs are later consumed by the background sync loop. `fetch_and_validate()` pulls `servers_info` for the store, shuffles them, and for each one calls `insert_from_delta_file()` with the raw `server_info.url`: [2](#0-1) 

`insert_from_delta_file()` (in `download_data.py`) drives `download_file()`, which, absent a downloader plugin, calls `http_download()` directly against `server_info.url + "/" + filename`, with only a `proxy_url` and timeout — no host/IP allow-list, no blocking of loopback/link-local/RFC1918 ranges, no restriction on scheme beyond what `aiohttp` accepts: [3](#0-2) [4](#0-3) 

The only content check applied after the request completes is a maximum file size limit; the request itself (to `http://127.0.0.1:<port>/...`, cloud metadata endpoints such as `http://169.254.169.254/...`, or other internal-only services) is always issued by the node process. The project's own documentation for this module explicitly names mirror URLs as "external trust inputs" that "must continue to be verified against wallet-advertised roots" for *data integrity*, but does not describe any URL-destination validation for *request-issuance* safety: [5](#0-4) 

This mirrors the reported Superset bug class: a permissioned but not fully trusted caller (anyone with DataLayer RPC access, e.g., a wallet/DataLayer client) supplies an import/fetch source URL, and the server performs the fetch on the caller's behalf without restricting the destination, enabling SSRF against internal services reachable from the node host.

### Impact Explanation
An RPC caller with DataLayer access (the RPC surface explicitly reachable per the in-scope list: "Data Layer client") can register a subscription pointing at an internal-only endpoint (localhost admin panels, cloud metadata service, internal-only databases with HTTP APIs, other services on a private network). The periodic `update_subscription()`/`fetch_and_validate()` loop will then repeatedly issue authenticated-context HTTP GET requests to that endpoint from the node's network position, and any response headers/content-length/body up to the size limit are processed by the node — enabling network reconnaissance, internal service interaction, or potentially credential/metadata exfiltration depending on deployment environment (e.g., cloud IMDS). This is classified Medium in line with the original Superset finding since it requires an authorized/authenticated caller but crosses a trust boundary into internal network resources.

### Likelihood Explanation
Any client with access to the DataLayer RPC (`subscribe` endpoint) can trivially trigger this: no permission distinctions exist within the DataLayer RPC beyond having the RPC credential itself, and the sync loop runs continuously in the background (`periodically_manage_data()`), guaranteeing the SSRF request will fire without further attacker action.

### Recommendation
Validate and restrict subscription URLs before persisting/using them in `DataLayer.subscribe()` and before each fetch in `download_data.http_download()`/`download_file()`: enforce an allow-list of schemes (e.g. `http`/`https` only, reject `file://`, `s3://` handled separately with plugin-only path), resolve and reject requests targeting loopback, link-local (including `169.254.0.0/16`), and RFC1918 private ranges unless explicitly configured for test/trusted environments, and re-validate on redirect (disable automatic redirect following or re-check the final host).

### Proof of Concept
1. Obtain DataLayer RPC access (local RPC caller).
2. Create or use an existing store id, then call the `subscribe` RPC with `urls=["http://169.254.169.254"]` (or `http://127.0.0.1:<internal-port>`), reaching `DataLayer.subscribe()`. [1](#0-0) 
3. Wait for the periodic sync loop; `fetch_and_validate()` picks the malicious URL from `get_available_servers_for_store()` and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, causing the node process to issue an HTTP GET to the attacker-chosen internal address. [3](#0-2) 
4. Observe (via response size/behavior/logged errors, or by pointing the URL at an attacker-controlled listener acting as a reflector to a target internal-only endpoint) that the node reaches internal network resources on the attacker's behalf.

### Citations

**File:** chia/data_layer/data_layer.py (L642-694)
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

            to_download = (
                await self.wallet_rpc.dl_history(
                    DLHistory(
                        launcher_id=store_id,
                        min_generation=uint32(root.generation + 1),
                        max_generation=singleton_record.generation,
                    )
                )
            ).history
            try:
                proxy_url = self.config.get("proxy_url", None)
                success = await insert_from_delta_file(
                    self.data_store,
                    store_id,
                    root.generation,
                    target_generation=singleton_record.generation,
                    root_hashes=[record.root for record in reversed(to_download)],
                    server_info=server_info,
                    client_foldername=self.server_files_location,
                    timeout=self.client_timeout,
                    log=self.log,
                    proxy_url=proxy_url,
                    downloader=await self.get_downloader(store_id, url),
                    group_files_by_store=self.group_files_by_store,
                    maximum_full_file_count=self.maximum_full_file_count,
                    max_delta_file_size=self.max_delta_file_size,
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

**File:** chia/data_layer/download_data.py (L110-146)
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

**File:** chia/data_layer/download_data.py (L298-323)
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
```

**File:** .cursor/context/data-layer.md (L87-89)
```markdown
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
- DataLayer wallet code depends on singleton CLVM structure, odd singleton amounts, lineage proofs, and offer solver field names. Changes in wallet puzzle drivers or offer summaries can break this module without direct edits here.
```
