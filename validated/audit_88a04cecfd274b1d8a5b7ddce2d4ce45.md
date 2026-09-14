Confirmed: `create_new_mirror` in `chia/data_layer/data_layer_wallet.py` publishes arbitrary `urls` on-chain via a `CREATE_COIN` memo with no format/host validation, and any Data Layer client that discovers this mirror through `update_subscriptions_from_wallet` will later have `http_download` (in `chia/data_layer/download_data.py`) issue an outbound `session.get(server_info.url + "/" + filename, ...)` to that exact attacker-supplied URL, with no scheme allowlist and no private/loopback/link-local IP blocking anywhere in the mirror/subscribe/download path.

### Title
DataLayer mirror/subscription URLs are fetched by the server without SSRF protections, allowing internal network scanning - (File: chia/data_layer/download_data.py)

### Summary
Any wallet user who owns (or can create) a DataLayer store can publish a mirror pointing at an attacker-chosen URL. When another Chia node with a DataLayer service subscribes to that store, it automatically issues outbound HTTP GET requests to that URL to try to sync deltas — with no validation that the URL does not target loopback, private, or link-local addresses. This mirrors the ClipBucket v5 SSRF bug class: user-supplied remote URLs are trusted and fetched server-side without any allowlist/deny-list on destination host.

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a `CREATE_COIN` with `memos=[[launcher_id, *(url for url in urls)]]` and pushes it on chain with zero validation of the `urls` content [1](#0-0) . The RPC entrypoint `dl_new_mirror` in `chia/wallet/wallet_rpc_api.py` and the DataLayer service `add_mirror()` also perform no URL scheme/host validation before publishing the mirror [2](#0-1) [3](#0-2) . This is confirmed by an in-repo test that publishes literal `http://127.0.0.1/8000` mirror URLs successfully [4](#0-3) .

Once published, any other node's `DataLayerWallet.coin_added()` decodes the mirror coin and stores the raw `urls` for tracked launcher ids [5](#0-4) . `DataLayer.update_subscriptions_from_wallet()` then pulls these mirror URLs from the wallet and feeds them straight into the local subscription/server list with no filtering [6](#0-5) . During the periodic sync loop, `fetch_and_validate()` selects one of these server URLs and calls `download_file()` [7](#0-6) , which in turn calls `http_download()`, performing `session.get(server_info.url + "/" + filename, ...)` [8](#0-7) . No code path validates that `server_info.url` is not a loopback/private/link-local/cloud-metadata address, and `proxy_url` is even passed through from local config, further widening the request surface.

### Impact Explanation
A regular, unprivileged wallet user (only needing enough XCH to pay the mirror-coin amount/fee) can force any other Chia node subscribed to that store's DataLayer to make server-initiated GET requests toward attacker-chosen hosts/ports on the victim's internal network (e.g., `127.0.0.1`, RFC1918 ranges, or cloud metadata endpoints like `169.254.169.254`). Response timing/errors (timeout vs. connection error vs. successful download attempt) can be used to fingerprint open internal services, matching the "internal network scan via SSRF" bug class from the ClipBucket advisory. This is a reachable, non-privileged Data Layer client/wallet-user path, not a malicious-peer or operator-only issue.

### Likelihood Explanation
Likelihood is limited by the requirement that a victim node must explicitly track/subscribe to the malicious store id (mirrors are only picked up for launcher ids the local wallet is already tracking) [9](#0-8) . However, subscribing to third-party DataLayer stores is a normal, expected DataLayer workflow, and once subscribed, the SSRF fetch is fully automatic via the periodic sync loop with no user confirmation of the destination host.

### Recommendation
Validate mirror/subscription URLs before persisting/using them: enforce an `http(s)` scheme, resolve the hostname, and reject loopback, private (RFC1918), link-local, and other non-routable/metadata address ranges (similar to SSRF-hardening patterns used elsewhere, e.g. `chia/util/network.py`/`chia/util/ip_address.py`) both when accepting `add_mirror`/`subscribe` input and again at `http_download()` time (in case of DNS rebinding), and expose an explicit user opt-in/allowlist for internal-network mirror URLs.

### Proof of Concept
1. Attacker creates a DataLayer store and publishes a mirror pointing at an internal victim address: `chia data add_mirror -i <store_id> -u http://169.254.169.254/latest/meta-data -a 1` (uses `add_mirror` RPC → `DataLayerWallet.create_new_mirror`).
2. Victim node runs `chia data subscribe -i <store_id>` for that store (a normal DataLayer client action).
3. Victim's `DataLayer.periodically_manage_data()` loop calls `update_subscriptions_from_wallet()`, which loads the attacker's URL as a candidate server, then `fetch_and_validate()`/`download_file()`/`http_download()` issues a GET request from the victim node directly to `http://169.254.169.254/...`, with the response/timing observable indirectly by the attacker republishing further mirrors and interpreting subsequent sync behavior/logs, effectively probing the victim's internal network.

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
