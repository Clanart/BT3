Confirmed: no URL validation/allowlist exists anywhere in the DataLayer mirror/download path — `http_download` in `chia/data_layer/download_data.py` performs a raw `aiohttp` GET to `server_info.url + "/" + filename` with no scheme or host restriction, and `is_filename_valid` only validates the *filename*, not the host.

### Title
Attacker-controlled on-chain mirror URLs let any wallet drive server-side HTTP requests from every subscribed Data Layer node (SSRF) - (File: chia/data_layer/download_data.py)

### Summary
Any wallet user can publish a "mirror" coin containing arbitrary URL strings for a DataLayer store, entirely on-chain and unauthenticated beyond normal fee payment. Every peer that subscribes to that store's DataLayer singleton (an ordinary, expected sync action) will parse those URLs and issue outbound HTTP GET requests to them from the DataLayer node process, with no scheme, host, or address-range validation — an authenticated/attacker-controlled SSRF analogous to the n8n-mcp webhook/API-client SSRF (GHSA-cmrh-wvq6-wm9r), where a URL supplied by a low-privilege party is fetched server-side without an SSRF gate.

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet holder create a `create_mirror_puzzle()` coin whose memos encode an arbitrary `launcher_id` plus a list of raw `url` strings supplied by the caller, with no validation of the URL contents. [1](#0-0) 

This is exposed directly through the `dl_new_mirror` wallet RPC and the `data add_mirror` CLI/RPC surface, both of which just forward the caller-supplied `urls` list unchanged. [2](#0-1) [3](#0-2) 

Once the mirror coin is confirmed on chain, any node syncing/subscribing to that store id treats the mirror URLs as legitimate download sources. `DataLayer.fetch_and_validate()` pulls `servers_info` (populated from these mirror URLs) and passes them into `insert_from_delta_file()` / `download_file()`. [4](#0-3) 

`download_file()` then calls `http_download()`, which issues a raw `aiohttp.ClientSession().get(server_info.url + "/" + filename, ...)` request — the URL is used verbatim, with no scheme allowlist, no localhost/RFC1918/link-local (`169.254.169.254`-class) blocking, and no DNS-rebinding protection. [5](#0-4) [6](#0-5) 

`is_filename_valid()`, the only validation referenced near this code, checks the *filename* format, not the destination host, so it provides no SSRF protection. [7](#0-6) 

The same untrusted-URL pattern also reaches the plugin/downloader path, which POSTs the mirror `server_info.url` as JSON to a configured downloader plugin without validating it either. [8](#0-7) 

### Impact Explanation
Any wallet user who can afford the mirror-coin fee can force every other node subscribing to (or auto-subscribing to, for owned stores) that DataLayer store to issue outbound HTTP requests to attacker-chosen destinations, including internal services and cloud metadata endpoints (`169.254.169.254`, etc.) reachable from the DataLayer host's network. Because `http_download()` streams the response body directly into the target delta file and separately triggers store insertion logic, and the plugin path returns/consumes the response JSON, this enables internal-service probing/data exfiltration from any node that decides to sync a maliciously mirrored store — a purely on-chain, cost-free-to-observe action requiring no direct network access to the victim. This matches the reachable-analog criteria (Data Layer roots and proofs / Data Layer client) explicitly in scope.

### Likelihood Explanation
High. Creating a mirror coin with attacker-controlled URLs is a normal, permitted wallet action requiring only a small on-chain fee/amount — no elevated privilege, no malicious peer/node relationship, and no protocol violation. Any node that subscribes to the store (which DataLayer's periodic sync loop does automatically for owned stores and per user configuration for subscribed stores) will process the URLs without any host-based filtering.

### Recommendation
Add an SSRF gate (equivalent to a `WEBHOOK_SECURITY_MODE`-style allow/deny policy) that validates mirror URLs — and any downloader/plugin URL derived from them — before they are used in `http_download()` and the plugin `download()` POST path: resolve the hostname, reject loopback/link-local/RFC1918/cloud-metadata address ranges by default (with an explicit opt-in for trusted private networks), and enforce an `http(s)` scheme allowlist. Apply the same validation both when mirror URLs are stored via `update_subscriptions_from_wallet()` and immediately before each outbound request in `download_data.py`, to also protect against DNS rebinding between validation and use.

### Proof of Concept
1. Wallet A creates a DataLayer store and publishes a mirror via `dl_new_mirror` (or `chia data add_mirror`) with `urls=["http://169.254.169.254/latest/meta-data/iam/security-credentials/"]` — `create_new_mirror()` accepts this with no validation. [1](#0-0) 
2. The mirror coin confirms on chain.
3. Any other node (e.g., an operator running a DataLayer service in a cloud VM) subscribes to that store id, either explicitly or via auto-subscription of an owned store whose mirrors it discovers.
4. That node's `update_subscription()` → `fetch_and_validate()` loop picks up the malicious mirror URL via `get_available_servers_for_store()` and calls `http_download()`, issuing a GET to the metadata URL from the victim host and writing the returned credentials into a local delta file that is subsequently processed. [9](#0-8)

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

**File:** chia/wallet/wallet_rpc_api.py (L3255-3274)
```python
    async def dl_new_mirror(
        self,
        request: DLNewMirror,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> DLNewMirrorResponse:
        """Add a new on chain message for a specific singleton"""
        if self.service.wallet_state_manager is None:
            raise ValueError("The wallet service is not currently initialized")

        dl_wallet = await self.service.wallet_state_manager.get_dl_wallet()
        async with self.service.wallet_state_manager.lock:
            await dl_wallet.create_new_mirror(
                request.launcher_id,
                request.amount,
                Mirror.encode_urls(request.urls),
                action_scope,
                fee=request.fee,
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

**File:** chia/data_layer/data_layer.py (L965-970)
```python
    async def add_mirror(self, store_id: bytes32, urls: list[str], amount: uint64, fee: uint64) -> None:
        if not urls:
            raise RuntimeError("URL list can't be empty")
        await self.wallet_rpc.dl_new_mirror(
            DLNewMirror(launcher_id=store_id, amount=amount, urls=urls, fee=fee, push=True), DEFAULT_TX_CONFIG
        )
```

**File:** chia/data_layer/download_data.py (L110-169)
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

**File:** chia/data_layer/data_layer_server.py (L15-15)
```python
from chia.data_layer.download_data import is_filename_valid
```
