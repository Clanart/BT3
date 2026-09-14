### Title
DataLayer mirror URLs from any coin memo trigger unauthenticated server-side HTTP fetches (SSRF) - ([File: chia/data_layer/download_data.py])

### Summary
DataLayer stores treat mirror URLs recorded in on-chain "mirror" coin memos as trusted server endpoints. Any wallet can create a mirror coin for *any* `store_id`/`launcher_id` with an attacker-chosen URL, and any node that subscribes to that store will have its DataLayer service issue outbound HTTP requests to that URL, with no validation of scheme, host, or destination (no protection against internal/loopback/link-local targets). This is the same bug class as CVE-2020-25820: attacker-supplied URL data drives an unauthenticated server-side fetch (SSRF).

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet create a "mirror" coin whose puzzle is `create_mirror_puzzle()` and whose memos encode an arbitrary `launcher_id` plus arbitrary URL bytes, with no check that the caller owns or controls that `launcher_id`/store: [1](#0-0) 

When any node observes that mirror coin (`coin_added`), it decodes `launcher_id, urls` straight from the spend's memos and stores them if the launcher is tracked, without validating the URL contents: [2](#0-1) 

The DataLayer service later syncs subscription server lists directly from these wallet-reported mirror URLs, only trimming trailing slashes, with no scheme/host allowlisting: [3](#0-2) 

During periodic sync, `fetch_and_validate()` iterates these attacker-controlled server URLs and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which performs an unrestricted `aiohttp` GET against `server_info.url`: [4](#0-3) [5](#0-4) 

There is no validation anywhere in this path rejecting loopback, link-local (e.g. cloud metadata `169.254.169.254`), or internal/private-network addresses before the outbound request is made; the only checks are on the downloaded delta-file's Merkle root/hash correctness after the fact, which does not prevent the SSRF request itself. The `s3_plugin_service.py` downloader restricts to `s3://` scheme, but the built-in HTTP path (`downloader is None`) has no such restriction.

### Impact Explanation
Any wallet user can force other Chia full nodes/DataLayer services that subscribe to (or are auto-subscribed to, e.g. owned stores/mirror lookups) a given store to make outbound HTTP requests to attacker-chosen hosts and ports. This enables SSRF against internal services reachable from the victim's host (internal admin endpoints, cloud instance metadata services, other local RPC ports), and can be used for internal network reconnaissance/port scanning or triggering unwanted internal actions. It also allows using a victim's DataLayer node as an anonymizing HTTP request proxy. This does not by itself forge coin identity or move funds, but it is a concrete SSRF against a reachable node process triggered purely by data recorded from a standard, unprivileged coin spend.

### Likelihood Explanation
The mirror-creation path (`create_new_mirror`/`dl_new_mirror` RPC) is available to any wallet holding XCH to pay the mirror-coin amount + fee, requires no special permission or ownership of the target store, and is exercised as a documented feature (mirrors) in existing tests. Any node tracking/subscribed to that `launcher_id` will pick up and act on the URL automatically during its periodic `periodically_manage_data()`/`fetch_and_validate()` loop, making exploitation straightforward and requiring no cooperation from the victim beyond normal DataLayer subscription behavior.

### Recommendation
Validate and restrict mirror/server URLs before they are used for outbound fetches: enforce an allowed scheme (https only, ideally), resolve and reject requests to loopback, link-local, and RFC1918/private address ranges (including after DNS resolution to prevent DNS-rebinding), and consider requiring explicit user opt-in/allowlisting of mirror hosts before `DataLayer` will automatically fetch from them, rather than trusting URLs sourced purely from on-chain coin memos.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (or `add_mirror` CLI/RPC) with `launcher_id` set to a victim's actively-subscribed/tracked DataLayer store id and `urls=["http://169.254.169.254/latest/meta-data/"]` (or an internal RPC/service URL), paying only the mirror coin amount + fee: [6](#0-5) 
2. Once the spend confirms, any node tracking that `launcher_id` records the mirror via `coin_added()`.
3. During its next sync cycle, that node's `DataLayer.fetch_and_validate()` reads this URL from `update_subscriptions_from_wallet()` and issues an outbound GET via `http_download()` to the attacker-chosen address, demonstrating SSRF.

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

**File:** chia/data_layer/data_layer_wallet.py (L775-800)
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
                await self.wallet_state_manager.add_interested_coin_ids([coin.name()])
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

**File:** chia/data_layer/data_layer.py (L979-986)
```python
    async def update_subscriptions_from_wallet(self, store_id: bytes32) -> None:
        mirrors: list[Mirror] = (await self.wallet_rpc.dl_get_mirrors(DLGetMirrors(launcher_id=store_id))).mirrors
        urls: list[str] = []
        for mirror in mirrors:
            urls += mirror.urls
        urls = [url.rstrip("/") for url in urls]
        await self.data_store.update_subscriptions_from_wallet(store_id, urls)

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
