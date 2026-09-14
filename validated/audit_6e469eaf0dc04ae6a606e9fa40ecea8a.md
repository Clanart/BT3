### Title
Data Layer mirror URLs are unvalidated attacker-controlled SSRF targets fetched by subscriber nodes - (File: chia/data_layer/data_layer.py)

### Summary
Any wallet user can publish an on-chain "mirror" coin for a DataLayer store containing arbitrary, completely unvalidated URL strings via `dl_new_mirror`. Any peer that subscribes to that store id will later have its local DataLayer service pull those URLs from wallet state and issue outbound HTTP requests to them with no scheme/host/port restrictions, matching the SSRF bug class in the reported CVE (unauthorized server-side requests originating from an authenticated party's action).

### Finding Description
`WalletRpcApi.dl_new_mirror()` accepts a caller-supplied `urls: list[str]` and forwards it unchanged into `dl_wallet.create_new_mirror(...)`, encoding it into the mirror coin's on-chain memo via `Mirror.encode_urls()`, with no URL/scheme/host validation: [1](#0-0) 

On the DataLayer service side, `add_mirror()` only checks that the list is non-empty, again with no URL validation, before pushing the mirror on-chain: [2](#0-1) 

Tests confirm arbitrary non-URL strings (`"foo"`, `"bar"`) are accepted end-to-end as mirror content, demonstrating there is no format enforcement anywhere in this path: [3](#0-2) 

Any other node subscribed to (or auto-subscribed to) that store id pulls these URLs directly from wallet mirror records into its local subscription list: [4](#0-3) 

The periodic sync loop then uses these attacker-supplied URLs as real download targets. `fetch_and_validate()` selects a `server_info.url` from stored subscription URLs and issues an outbound request: [5](#0-4) 

The actual network call is made in `download_file()`/`http_download()` (or in the downloader-plugin path via `aiohttp.ClientSession().post(downloader.url + "/download", ...)`), which performs the outbound HTTP request against the raw URL with no allow-list, no blocking of private/loopback/link-local addresses, and no redirect restrictions: [6](#0-5) 

`get_downloader()` similarly POSTs to `d.url + "/handle_download"` for every configured downloader plugin with the attacker-influenced target URL embedded in the JSON body: [7](#0-6) 

None of these code paths validate that the URL points to a legitimate external mirror server rather than an internal/local address (e.g. `http://127.0.0.1/`, `http://169.254.169.254/`, or an internal service port), and the project's own internal documentation explicitly acknowledges mirror URLs are untrusted input that is not validated at this layer:


### Impact Explanation
A wallet user (an "authenticated" DataLayer/wallet participant per the report's threat model) can publish a mirror listing arbitrary URLs, including internal-network or loopback targets, for any DataLayer store id. Any other node that later subscribes to (or is auto-subscribed to, per `pseudo-subscribes owned stores`/auto-subscribe logic) that store id will have its DataLayer service make outbound HTTP requests to those attacker-chosen destinations during `periodically_manage_data()`/`update_subscription()` cycles — enabling internal network enumeration, port scanning of the victim's local/internal network, or interaction with internal-only HTTP services/metadata endpoints from the victim's machine, consistent with the CVE's SSRF impact (network enumeration / facilitating other attacks). This matches the Medium severity and reachable-by-single-action nature of the referenced CVE.

### Likelihood Explanation
Likelihood is moderate-to-high in a DataLayer deployment: creating a mirror coin only requires a wallet with a small amount of XCH and a `dl_new_mirror`/`add_mirror` RPC call, and no code path validates or restricts the URL content before it becomes a subscription target for any peer that follows/subscribes to the store. Exploitation additionally depends on a victim node choosing to subscribe to (or auto-subscribing to) the attacker's store id, which is a normal, expected DataLayer usage pattern (subscribing to a store to replicate/verify its data), so the reachability bar is low in typical Data Layer operation.

### Recommendation
Add URL/host validation at both mirror-creation time (`WalletRpcApi.dl_new_mirror`, `DataLayer.add_mirror`) and mirror-consumption time (`update_subscriptions_from_wallet`, `fetch_and_validate`, `download_file`, `get_downloader`): enforce an allow-listed scheme (e.g., `https`/`http` only), reject loopback/link-local/private/multicast/metadata-service IP ranges (with DNS-resolution-time re-checks to prevent DNS-rebinding), and consider making outbound mirror fetches configurable/opt-in (e.g., require explicit user confirmation or an allow-list of trusted mirror hosts) rather than automatically dialing any URL a remote singleton owner publishes on-chain.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror`/`dl_new_mirror` with `urls=["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:6379/"]` (or any internal victim-reachable address), confirmed via `chia/_tests/core/data_layer/test_data_rpc.py::test_mirrors` showing arbitrary strings are accepted as mirror URLs: [8](#0-7) 
2. Victim node subscribes to the attacker's store id (e.g., via `DataLayerRpcApi.subscribe`) for legitimate replication purposes, as shown in the offer/mirror sync test flow: [9](#0-8) 
3. On the victim's next `periodically_manage_data()` cycle, `update_subscriptions_from_wallet()` copies the attacker's mirror URLs into the local subscription table, and `fetch_and_validate()`/`download_file()` issue an outbound HTTP request to the attacker-chosen internal address from the victim's machine, without any restriction on target host.

### Citations

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

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L965-966)
```python
        await maker.api.subscribe(request={"id": taker.id.hex(), "urls": ["http://127.0.0.1/8000"]})
        await taker.api.subscribe(request={"id": maker.id.hex(), "urls": ["http://127.0.0.1/8000"]})
```

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2782-2790)
```python
        urls = ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        res = await data_rpc_api.add_mirror({"id": store_id.hex(), "urls": urls, "amount": 1, "fee": 1})

        await farm_block_check_singleton(data_layer, full_node_api, ph, store_id, wallet=wallet_rpc_api.service)
        mirrors = await data_rpc_api.get_mirrors({"id": store_id.hex()})
        mirror_list = mirrors["mirrors"]
        assert len(mirror_list) == 1
        mirror = mirror_list[0]
        assert mirror["urls"] == ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
```
