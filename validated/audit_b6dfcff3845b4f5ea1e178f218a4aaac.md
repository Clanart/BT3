### Title
SSRF in DataLayer HTTP mirror download - ([File: chia/data_layer/download_data.py])

### Summary
Analogous to CVE-2026-9081 (Langflow `validate_model_provider_key()` passing a user-supplied `OLLAMA_BASE_URL` straight into `requests.get()` with no host/scheme/private-IP filtering), Chia's DataLayer subscription/mirror-download path takes an operator/RPC-supplied server URL and passes it directly into an outbound HTTP request with no validation of scheme, host, or private/loopback address ranges.

### Finding Description
A local RPC caller can add arbitrary mirror URLs for a DataLayer store via the `/subscribe` RPC endpoint (`chia/data_layer/data_layer_rpc_api.py`, `subscribe()`), which forwards the caller-supplied `urls` list unchanged into `DataLayer.subscribe()`: [1](#0-0) 

`DataLayer.subscribe()` stores each URL verbatim as a `ServerInfo(url, 0, 0)` with no scheme or host filtering: [2](#0-1) 

During the periodic sync loop, `fetch_and_validate()` selects one of these stored server URLs and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which builds the request target by simple string concatenation of the stored URL and issues an `aiohttp` GET with no allowlist, scheme check, or private-IP/loopback/link-local filtering: [3](#0-2) [4](#0-3) 

I searched `chia/data_layer/*.py` for any `urlparse`, `ip_address`, `private`, or `localhost` filtering applied to subscription/mirror URLs and found none in `data_layer.py`, `data_layer_rpc_api.py`, or `download_data.py` (the only such checks exist in `s3_plugin_service.py`, which only validates the `s3://` scheme for its own plugin path, not for the plain-HTTP `http_download()` path used by default). This mirrors the CVE's root cause: a user/operator-controlled URL is fed unchanged into an HTTP client call without SSRF hardening.

### Impact Explanation
An unprivileged local caller of the DataLayer RPC (or any code path that can register subscriptions/mirrors, including remote DataLayer offer-driven mirror registration flows) can point the DataLayer service at `http://127.0.0.1:<port>/...`, cloud metadata endpoints (`http://169.254.169.254/...`), or other internal-only services. Because `periodically_manage_data()` runs this fetch loop automatically and unauthenticated by the target, and the response is written to disk under the DataLayer service's own directories and parsed by `insert_into_data_store_from_file()`, this provides a path to internal network reconnaissance/SSRF from a service that is otherwise reachable only via localhost RPC, and it also causes outbound requests against internal infrastructure that would otherwise be firewalled from external triggering.

### Likelihood Explanation
Moderate-to-high: reaching this requires access to the DataLayer RPC (`/subscribe`), which is treated as a local/trusted-caller surface but is exactly the "local RPC caller" class in scope. No additional privilege beyond calling the DataLayer RPC is needed, and the periodic background loop (`periodically_manage_data`) will automatically dispatch the SSRF request without further interaction.

### Recommendation
Validate and constrain subscription/mirror URLs before persisting or using them for `http_download()`: enforce an `http`/`https` scheme allowlist, resolve the hostname and reject loopback/RFC1918/link-local/multicast ranges (mirroring the existing `is_localhost()`/`is_in_network()`/`ip_address` helpers already used for peer validation in `chia/util/network.py`), and re-validate at request time (not just at subscribe time) to defend against DNS rebinding.

### Proof of Concept
1. Call the DataLayer RPC `subscribe` endpoint with `{"id": "<store_id>", "urls": ["http://127.0.0.1:<internal-port>"]}`.
2. Wait for `periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()` to run (default `manage_data_interval` = 60s).
3. Observe `http_download()` in `chia/data_layer/download_data.py` issuing a GET to `http://127.0.0.1:<internal-port>/<delta-filename>`, demonstrating the DataLayer service can be coerced into contacting arbitrary internal/loopback addresses supplied by the RPC caller.

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

**File:** chia/data_layer/download_data.py (L298-319)
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
```
