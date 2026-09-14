### Title
SSRF via unrestricted DataLayer mirror URLs fetched during file download - (`File: chia/data_layer/download_data.py`)

### Summary
Any wallet owner can publish an arbitrary URL string on-chain as a "mirror" for a Data Layer store by spending a small amount of XCH. Any other node that subscribes to that store will merge the attacker-controlled URL into its subscription server list and later issue outbound `aiohttp` HTTP GET requests to that exact URL with no scheme, host, or network-reachability validation — a server-side request forgery analogous to the PublicCMS `catchimage` SSRF (CVE-2024-40543), where a user-supplied URL is fetched server-side without restriction.

### Finding Description
A mirror is created by `DataLayerWallet.create_new_mirror`, which takes a list of raw `urls: list[bytes]` and writes them unchanged into the coin memo with no validation of scheme or destination: [1](#0-0) 

This is reachable from the wallet RPC `dl_new_mirror`, which only encodes the URLs and forwards them, again without validation: [2](#0-1) 

The CLI/RPC `add_mirror` path in the Data Layer service only checks that the URL list isn't empty — no scheme allow-list, no blocking of loopback/link-local/private ranges, no cloud-metadata protection: [3](#0-2) [4](#0-3) 

Once the mirror coin is confirmed on-chain, any peer that subscribes to the corresponding `store_id` merges the mirror's URLs into its subscription server list via `update_subscriptions_from_wallet`: [5](#0-4) 

During sync, `fetch_and_validate` iterates over `servers_info` (populated from those attacker-supplied URLs) and calls `insert_from_delta_file`, which eventually calls `http_download`: [6](#0-5) 

`http_download` performs an unrestricted `aiohttp` GET directly against the attacker-controlled `server_info.url`, with no validation that the target is a legitimate external mirror server (no blocking of `127.0.0.1`, RFC1918 ranges, `169.254.169.254`, or non-HTTP(S) schemes): [7](#0-6) 

The plugin-based downloader path (`get_downloader`, `download_file`) similarly forwards `server_info.url` to a plugin over POST without validating the URL's target: [8](#0-7) [9](#0-8) 

### Impact Explanation
Any wallet holder (an unprivileged actor who only needs enough mojos to pay a fee) can force any Data Layer node subscribed to a shared store to send outbound HTTP requests to an arbitrary attacker-chosen destination. This enables classic SSRF outcomes: scanning/probing the victim's internal network, hitting cloud metadata endpoints to exfiltrate credentials, interacting with internal-only services (other local RPC ports, internal admin panels), or triggering requests against third parties for abuse/DoS amplification. Because the URL travels through consensus-confirmed on-chain data (a coin memo), it persists and will be re-fetched by every subscribed peer, not just a single victim.

### Likelihood Explanation
Likelihood is high for any operator who runs Data Layer and subscribes to third-party stores (the intended, documented use case for public mirrors). Creating a malicious mirror only requires a standard wallet transaction with a nominal fee — no special privileges, no consensus rule violation, and no interaction with the victim beyond them choosing to subscribe to the store, which is the normal Data Layer workflow.

### Recommendation
Validate mirror/server URLs before they are ever fetched: restrict scheme to `http`/`https`, resolve and reject requests to loopback, link-local, private, and cloud-metadata address ranges (SSRF-safe egress filtering) both at mirror-creation time (`create_new_mirror`/`add_mirror`) as a courtesy check, and — critically — at fetch time in `http_download`/`get_downloader`/`download_file`, since on-chain data cannot be trusted to have been validated by the creator. Consider requiring an explicit user-approved allow-list of mirror hosts before a subscribing node performs an outbound request to one.

### Proof of Concept
1. Attacker wallet runs `chia data add_mirror --id <store_id> --url http://169.254.169.254/latest/meta-data/ -a 1 -m 1` (or `http://127.0.0.1:<internal-port>/...`), confirming a mirror coin on-chain with the malicious URL in its memo (`chia/data_layer/data_layer_wallet.py:687-703`, `chia/cmds/data.py:474-511`).
2. A victim operator subscribes their Data Layer node to `store_id` (a supported, expected workflow) and calls `update_subscriptions_from_wallet`, which pulls in the malicious URL (`chia/data_layer/data_layer.py:979-985`).
3. On the next sync cycle, `fetch_and_validate` selects the malicious `ServerInfo` and calls `insert_from_delta_file` → `download_file` → `http_download`, which issues `session.get("http://169.254.169.254/latest/meta-data/" + filename, ...)` from the victim's node (`chia/data_layer/data_layer.py:642-694`, `chia/data_layer/download_data.py:298-324`), demonstrating the SSRF against an internal/metadata target chosen entirely by the attacker.

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

**File:** chia/data_layer/data_layer_rpc_api.py (L468-475)
```python
    async def add_mirror(self, request: dict[str, Any]) -> EndpointResult:
        store_id = request["id"]
        id_bytes = bytes32.from_hexstr(store_id)
        urls = request["urls"]
        amount = request["amount"]
        fee = get_fee(self.service.config, request)
        await self.service.add_mirror(id_bytes, urls, amount, fee)
        return {}
```

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
