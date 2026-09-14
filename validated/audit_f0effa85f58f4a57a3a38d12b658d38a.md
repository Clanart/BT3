### Title
Authenticated SSRF via DataLayer `subscribe`/mirror URL functionality allows arbitrary internal HTTP requests from the node - (File: chia/data_layer/data_layer.py)

### Summary
The DataLayer service exposes a local `subscribe` RPC that accepts arbitrary, user-supplied URLs as mirror/server endpoints for a store. Those URLs are persisted unvalidated and later dereferenced by the DataLayer background sync loop and RPC-triggered fetch paths, which issue outbound HTTP requests to them. No hostname/IP allow-listing, DNS-resolution check, or private-network blocking is performed before the service connects to attacker-controlled destinations, mirroring the Xibo CMS SSRF bug class where an authorized-but-limited user can make the server issue requests to internal-only endpoints.

### Finding Description
`DataLayer.subscribe()` takes a list of caller-supplied `urls`, strips trailing slashes, and stores them as `ServerInfo` records with no validation of scheme, host, or target network: [1](#0-0) 

These subscription URLs are later consumed by `fetch_and_validate()`, which iterates over `get_available_servers_for_store()` results and, for each, calls `insert_from_delta_file()` → `download_file()` → `http_download()` using the stored URL directly: [2](#0-1) 

The actual outbound request is built by concatenating the caller-controlled URL with a filename and performing a plain `aiohttp` GET, with no host/IP filtering: [3](#0-2) 

Additionally, `get_downloader()` in the same service POSTs a JSON body (including the caller-influenced `url`) to every configured plugin remote, and `download_file()`'s plugin path similarly POSTs the raw server URL to a plugin endpoint without validation: [4](#0-3) [5](#0-4) 

Repository-wide search found no use of the existing `is_localhost`/`is_trusted_cidr`/`resolve` helpers from `chia/util/network.py` anywhere in the `chia/data_layer/` module, confirming there is no SSRF mitigation applied to mirror or plugin URLs before they are dereferenced.

### Impact Explanation
Any local RPC caller with access to the DataLayer RPC (analogous to a Xibo user with DataSet-creation privilege — a scoped, non-admin capability) can register a mirror URL pointing at internal-only services (e.g., other localhost-bound RPC ports, internal management interfaces, or cloud metadata endpoints such as `http://169.254.169.254/`) and trigger `fetch_and_validate` (automatically via the periodic sync loop, or on demand through `subscribe`+chain activity). The DataLayer process will then issue authenticated-context HTTP GET/POST requests to that destination, potentially exfiltrating response data back into node logs or subscription state, or probing/interacting with internal services that have no independent authentication. This is a server-side request forgery reachable from a semi-trusted local caller, matching the CVE's medium-severity SSRF class (network reconnaissance/internal service interaction), not a full unauthenticated remote compromise.

### Likelihood Explanation
Likelihood is moderate: the caller must have access to the DataLayer RPC surface (a local/semi-trusted admin API per the RPC context notes), but no additional privilege beyond invoking `subscribe`/`update_data_store` is required — there's no extra permission gate distinguishing "can add mirrors" from general DataLayer RPC access. Because `fetch_and_validate` runs automatically in the periodic `manage_data` loop once a subscription exists, exploitation does not require further interactive steps after registering the malicious URL.

### Recommendation
Validate and restrict subscription/mirror/plugin URLs before they are stored or dereferenced: resolve the hostname and reject requests targeting loopback, link-local (including cloud metadata ranges), and other private/reserved address ranges using the existing `chia/util/network.py` helpers (`resolve`, `is_localhost`, `is_trusted_cidr`), enforce an allow-list/deny-list policy configurable by the node operator, and apply the same checks to plugin (`downloader`/`uploader`) URLs in `get_downloader()`/`download_file()`, not just mirror server URLs.

### Proof of Concept
1. As a caller with DataLayer RPC access, call `subscribe` with `urls=["http://169.254.169.254/latest/meta-data/"]` (or any internal-only host) for an owned/tracked store id — this succeeds unconditionally per `DataLayer.subscribe()`.
2. Wait for (or otherwise trigger) `periodically_manage_data()` to invoke `update_subscription()` → `fetch_and_validate()` for that store.
3. Observe that `http_download()` issues a GET to the attacker-chosen URL with no restriction, confirmed by the direct URL concatenation and lack of any network-scope check in `chia/data_layer/download_data.py` lines 298–324.

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

**File:** chia/data_layer/download_data.py (L147-165)
```python
    log.info(f"Using downloader {downloader} for store {store_id.hex()}.")
    request_json = {
        "url": server_info.url,
        "client_folder": str(client_foldername),
        "filename": filename,
        "group_files_by_store": group_downloaded_files_by_store,
        "max_delta_file_size": max_delta_file_size,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                downloader.url + "/download",
                json=request_json,
                headers=downloader.headers,
                timeout=timeout,
            ) as response:
                res_json = await response.json()
                assert isinstance(res_json["downloaded"], bool)
                return res_json["downloaded"]
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
