### Title
Semi-blind SSRF via unauthenticated DataLayer mirror URLs triggering outbound HTTP requests - (File: chia/data_layer/download_data.py)

### Summary
Any DataLayer participant can publish an arbitrary URL on-chain as a "mirror" for a store they do not need special privileges to create. Any other node that subscribes to that store's data will, as part of its normal periodic sync loop, issue outbound HTTP requests (and for plugin-configured nodes, POST requests) directly to that attacker-supplied URL with no validation of scheme, host, or destination (no blocking of loopback/link-local/internal addresses). This mirrors the DHIS2 CVE-2022-41949 pattern: a low-privilege actor supplies a URL that causes the server to make requests to arbitrary internal or external targets, useful for port-scanning internal infrastructure or probing for the existence of internal-only services.

### Finding Description
Mirror URLs are stored fully on-chain as coin memos and are not restricted in any way: [1](#0-0) 
`add_mirror` on the `DataLayer` service simply requires a non-empty URL list and forwards it straight to the wallet, with no scheme/host allow-list or private-IP filtering: [2](#0-1) 

Once a mirror is published for a `store_id`, every node that is subscribed to (i.e., tracking) that store will pick up the mirror's URLs during its periodic sync and treat them as legitimate download servers: [3](#0-2) 

These URLs then flow into `fetch_and_validate()`, which iterates over them and calls `insert_from_delta_file()` → `download_file()`: [4](#0-3) 

`download_file()` performs an outbound `aiohttp` GET directly against the attacker-controlled URL with no destination validation, or (if a downloader plugin is configured) a POST containing the URL to the plugin's `/download` endpoint: [5](#0-4) 

The actual HTTP GET is performed in `http_download()`, which concatenates `server_info.url` with a filename and issues the request without checking the resolved host against private/loopback/link-local ranges: [6](#0-5) 

Because any wallet user can call `dl_new_mirror` for any store id they can reference (the RPC does not restrict which store a mirror can target, only requiring the caller to pay the coin amount/fee for their own spend), and because subscribing to a store is a normal, low-privilege DataLayer client action, an attacker can plant malicious mirror URLs that will be fetched by any peer who later subscribes to (or already tracks) that store.

### Impact Explanation
This is a semi-blind SSRF: the requesting node's DataLayer service makes real outbound HTTP(S) requests to attacker-chosen URLs (e.g., `http://127.0.0.1:<port>/...`, `http://169.254.169.254/...`, or internal-only hostnames). While the response body is only interpreted as a delta file (and normal parsing/hash validation will reject non-DataLayer content), the timing/behavior of success vs. failure (`server_misses_file` vs. `received_correct_file`, and exception types such as `ClientConnectorError` vs. `ClientResponseError`/timeout) is observable and distinguishable by any DataLayer client through the ordinary subscription/sync logs or API status, enabling network reconnaissance of internal services reachable from the node (open-port detection, existence checks) without any additional privilege beyond being a DataLayer participant. If a downloader plugin is configured, the request additionally becomes a same-network SSRF pivot through the plugin's `/download` POST call.

### Likelihood Explanation
Likelihood is high for any deployment where DataLayer mirrors/subscriptions are used across mutually untrusting participants (the intended trust model for DataLayer, which supports open publish/subscribe of stores). Creating a malicious mirror only requires an on-chain spend of the mirror-coin amount (can be `0`), i.e., minimal cost, and any node that subscribes to the poisoned store's `store_id` will automatically and periodically attempt the outbound fetch as part of `periodically_manage_data()`'s normal control loop — no special user interaction beyond subscribing to the (potentially attractive/public) data store is required.

### Recommendation
- Validate and restrict mirror/server URLs before they are used to make outbound requests: enforce an allow-listed scheme (http/https only), resolve and reject requests to loopback, link-local, and RFC1918/private address ranges (unless the operator explicitly opts in via a `test`/`local` mode), and consider disallowing redirects to such ranges as well.
- Apply the same validation to plugin-forwarded `url` fields in `get_downloader()`/`download_file()`.
- Consider adding a configuration option to disable following mirror URLs entirely, or to require an operator-approved mirror allow-list, given mirrors are inherently untrusted third-party input per the module's own trust-boundary documentation.

### Proof of Concept
1. Attacker creates (or already owns) a DataLayer store and publishes a mirror pointing at an internal address, e.g. via the existing test flow: [7](#0-6) 
using a URL such as `http://127.0.0.1:22` or an internal service address instead of `"foo"`/`"bar"`.
2. Victim node subscribes to the attacker's `store_id` (a routine DataLayer client action): [8](#0-7) 
3. During the victim's periodic sync (`update_subscription()` → `update_subscriptions_from_wallet()` → `fetch_and_validate()`), the victim's node issues an outbound GET request to the attacker-chosen URL: [9](#0-8) 
4. The victim's logs/RPC state (`server_misses_file` count, connection error vs. timeout vs. HTTP error) reveal whether the target host/port responded, allowing the attacker to enumerate reachable internal endpoints from the victim's network position.

### Citations

**File:** chia/data_layer/data_layer_wallet.py (L687-703)
```python
    async def create_new_mirror(
        self,
        launcher_id: bytes32,
        amount: uint64,
        urls: list[bytes],
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        await self.standard_wallet.generate_signed_transaction(
            amounts=[amount],
            puzzle_hashes=[create_mirror_puzzle().get_tree_hash()],
            action_scope=action_scope,
            fee=fee,
            memos=[[launcher_id, *(url for url in urls)]],
            extra_conditions=extra_conditions,
        )
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

**File:** chia/data_layer/data_layer.py (L965-970)
```python
    async def add_mirror(self, store_id: bytes32, urls: list[str], amount: uint64, fee: uint64) -> None:
        if not urls:
            raise RuntimeError("URL list can't be empty")
        await self.wallet_rpc.dl_new_mirror(
            DLNewMirror(launcher_id=store_id, amount=amount, urls=urls, fee=fee, push=True), DEFAULT_TX_CONFIG
        )
```

**File:** chia/data_layer/data_layer.py (L979-985)
```python
    async def update_subscriptions_from_wallet(self, store_id: bytes32) -> None:
        mirrors: list[Mirror] = (await self.wallet_rpc.dl_get_mirrors(DLGetMirrors(launcher_id=store_id))).mirrors
        urls: list[str] = []
        for mirror in mirrors:
            urls += mirror.urls
        urls = [url.rstrip("/") for url in urls]
        await self.data_store.update_subscriptions_from_wallet(store_id, urls)
```

**File:** chia/data_layer/data_layer.py (L1102-1112)
```python
    async def update_subscription(
        self,
        worker_id: int,
        job: Job[Subscription],
    ) -> None:
        subscription = job.input

        try:
            await self.update_subscriptions_from_wallet(subscription.store_id)
            await self.fetch_and_validate(subscription.store_id)
            await self.upload_files(subscription.store_id)
```

**File:** chia/data_layer/download_data.py (L110-168)
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
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        log.error(f"download_file could not get response from plugin {downloader}: {type(e).__name__}: {e}")
        return False
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

**File:** chia/_tests/wallet/rpc/test_dl_wallet_rpc.py (L281-292)
```python
            txs = (
                await client.dl_new_mirror(
                    DLNewMirror(
                        launcher_id=launcher_id,
                        amount=uint64(1000),
                        urls=["foo", "bar"],
                        fee=uint64(2000000000000),
                        push=True,
                    ),
                    DEFAULT_TX_CONFIG,
                )
            ).transactions
```
