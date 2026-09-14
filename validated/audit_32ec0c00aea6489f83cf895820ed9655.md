### Title
Server-Side Request Forgery via unauthenticated on-chain DataLayer mirror URLs - (File: `chia/data_layer/data_layer.py`)

### Summary
Chia's Data Layer subsystem allows any wallet participant to publish "mirror" URLs for a store on-chain via a coin spend [1](#0-0) . Any node that tracks/subscribes to that store will parse these attacker-supplied URL strings out of the mirror coin's spend and automatically issue outbound HTTP requests to them with no scheme or destination validation, mirroring the CWE-918 SSRF pattern in the referenced OpenRefine advisory (arbitrary user-supplied URL fetched by the server).

### Finding Description
A mirror coin's spend encodes a `launcher_id` and a list of arbitrary `urls`, which is decoded and stored verbatim by `DataLayerWallet.coin_added`: `launcher_id, urls = get_mirror_info(parent_spend.puzzle_reveal, parent_spend.solution)` followed by `Mirror.decode_urls(urls)` [2](#0-1) . These mirror coins can be created by any wallet holding funds via the `dl_new_mirror` RPC / `add_mirror` DataLayer RPC, which only rejects an empty URL list — no scheme allow-list, no hostname/IP validation: `if not urls: raise RuntimeError("URL list can't be empty")` [3](#0-2) .

Once mirrors are visible on-chain, `DataLayer.update_subscriptions_from_wallet` pulls all mirror URLs for a tracked store and stores them as subscription servers without any filtering: `urls += mirror.urls` then `await self.data_store.update_subscriptions_from_wallet(store_id, urls)` [4](#0-3) .

When a node later syncs that store (`fetch_and_validate`), it iterates over these `servers_info` (built from `get_available_servers_for_store`, populated from the same on-chain mirror URLs) and calls `insert_from_delta_file(...)` with `server_info=server_info` for each URL [5](#0-4) . `insert_from_delta_file` ultimately calls `download_file`, which — absent a configured downloader plugin — performs `await http_download(target_filename_path, filename, proxy_url, server_info, timeout, log, max_delta_file_size)` directly against `server_info.url`, the attacker-chosen string [6](#0-5) . There is no restriction preventing `server_info.url` from pointing at `http://127.0.0.1:<internal-rpc-port>/...`, a link-local metadata endpoint, or other internal-only services reachable from the node's network namespace; the code merely tries/excepts connection errors and otherwise proceeds to fetch and parse the response as delta-file content.

The `subscribe` RPC path exhibits the same pattern: `DataLayerRpcClient.subscribe(store_id, urls)` forwards a caller-supplied list of arbitrary `urls` directly into the DataLayer's server list with no validation [7](#0-6) , which is later fed into the same `http_download`/`get_downloader` flow.

### Impact Explanation
This is a genuine SSRF: an unprivileged actor (any wallet holder able to spend a small amount of mojos to create a mirror coin, or any local RPC caller invoking `subscribe`/`add_mirror`) can cause any peer that tracks the corresponding DataLayer store to issue attacker-controlled outbound HTTP requests. Because DataLayer nodes often run alongside a full node/wallet exposing local-only RPC endpoints, this can be used to probe or interact with internal services (e.g., local RPC ports, cloud metadata services, other local daemons) that are not otherwise reachable from the network, disclosing internal state or triggering unintended side effects on those internal HTTP endpoints. This matches the CWE-918/OpenRefine bug class of unauthenticated user input driving an outbound fetch with no destination restriction.

### Likelihood Explanation
Likelihood is moderate-to-high for any operator running a DataLayer node that subscribes to third-party/public stores (the primary intended use case of DL mirrors — sharing stores across independently-operated nodes). Creating a malicious mirror only requires a minimal on-chain spend, and any node that later chooses to track that `store_id` (a normal, expected workflow) will automatically fetch from the attacker's URLs without any human review of the URL content.

### Recommendation
Validate and restrict mirror/subscription URLs before use: enforce an `http(s)` scheme allow-list, resolve and reject requests to private/loopback/link-local/multicast IP ranges (RFC1918, 127.0.0.0/8, 169.254.0.0/16, etc.) and disallow DNS names resolving to such ranges at request time (not just at input time, to avoid DNS rebinding), and consider requiring operator opt-in/allow-listing of mirror hosts rather than trusting on-chain-supplied URLs implicitly. Apply the same validation uniformly in `DataLayer.add_mirror`, the `subscribe` RPC path, and immediately before the `http_download` call in `chia/data_layer/download_data.py`.

### Proof of Concept
1. Attacker creates a DataLayer store mirror pointing at an internal target, e.g. via RPC: `add_mirror(id=<store_id>, urls=["http://127.0.0.1:<internal-rpc-port>/some_endpoint"], amount=1, fee=1)` [8](#0-7) , broadcasting the resulting spend bundle to the mempool.
2. A victim node tracks/subscribes to the same `store_id` (normal DL usage) and calls `update_subscriptions_from_wallet`, which pulls in the malicious URL as a server candidate [4](#0-3) .
3. On the next sync cycle, `fetch_and_validate` -> `insert_from_delta_file` -> `download_file` -> `http_download` issues an HTTP request to `http://127.0.0.1:<internal-rpc-port>/...` from the victim node's process, without any destination validation [9](#0-8) .

Note: I was unable to view the body of `http_download` itself (only its call sites) due to index size limits; a full Devin session with filesystem access would be needed to confirm whether any partial destination filtering exists inside that function before concluding definitively that no mitigation exists at that layer.

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

**File:** chia/data_layer/data_layer_wallet.py (L775-799)
```python
    async def coin_added(
        self, coin: Coin, height: uint32, peer: WSChiaConnection, coin_data: object | None, sync_scope: WalletSyncScope
    ) -> None:
        if coin.puzzle_hash == create_mirror_puzzle().get_tree_hash():
            parent_state: CoinState = (
                await self.wallet_state_manager.wallet_node.get_coin_state([coin.parent_coin_info], peer=peer)
            )[0]
            parent_spend = await fetch_coin_spend(height, parent_state.coin, peer)
            assert parent_spend is not None
            launcher_id, urls = get_mirror_info(parent_spend.puzzle_reveal, parent_spend.solution)
            # Don't track mirrors with empty url list.
            if not urls:
                return
            if await self.wallet_state_manager.dl_store.is_launcher_tracked(launcher_id):
                ours: bool = await self.wallet_state_manager.get_wallet_for_coin(coin.parent_coin_info) is not None
                await self.wallet_state_manager.dl_store.add_mirror(
                    Mirror(
                        coin.name(),
                        launcher_id,
                        uint64(coin.amount),
                        Mirror.decode_urls(urls),
                        ours,
                        height,
                    )
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

**File:** chia/data_layer/data_layer_rpc_client.py (L107-109)
```python
    async def subscribe(self, store_id: bytes32, urls: list[str]) -> dict[str, Any]:
        response = await self.fetch("subscribe", {"id": store_id.hex(), "urls": urls})
        return response
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
