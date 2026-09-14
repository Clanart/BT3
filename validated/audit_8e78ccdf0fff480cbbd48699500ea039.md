This confirms the vulnerability path. `DataLayerWallet.coin_added()` at [1](#0-0)  tracks a mirror coin for **any** `launcher_id` extracted from the mirror's memos as long as that launcher is already tracked locally — it does not check that the mirror creator owns or controls the DataLayer store. Any unprivileged wallet user can call `dl_new_mirror` (via `DataLayerWallet.create_new_mirror()`) with an arbitrary `launcher_id` and arbitrary URL strings placed in coin memos, with no ownership check on the launcher_id and no URL validation, as shown in `create_new_mirror()`.

### Title
DataLayer Confused Deputy / SSRF via Attacker-Controlled Mirror URLs - (File: chia/data_layer/download_data.py)

### Summary
Any wallet user can push an on-chain "mirror" coin for **any** DataLayer store (`launcher_id`) they don't own, embedding arbitrary URLs in the coin's memos via `dl_new_mirror`/`create_new_mirror`. These URLs are picked up by every node that tracks/subscribes to that store and are later fetched by the `DataLayer` service's HTTP client without host/scheme restrictions, causing the victim's node to issue outbound requests to attacker-chosen destinations (e.g., internal services, localhost, cloud metadata endpoints).

### Finding Description
`DataLayerWallet.create_new_mirror()` creates a `CREATE_COIN` spend to the mirror puzzle hash, memoizing `[launcher_id, *urls]` with no restriction on which `launcher_id` is targeted and no validation of the `urls` content [2](#0-1) . This is exposed unauthenticated to any local wallet RPC caller and, more importantly, to anyone able to submit a spend bundle on chain that creates such a coin, since the wallet layer only requires ownership of the coins being spent to pay for it — not ownership of the target `launcher_id`.

When any node observes this coin, `DataLayerWallet.coin_added()` decodes the memos and, if the `launcher_id` is already tracked locally (i.e., the node subscribes to or owns that DataLayer store), unconditionally records the mirror with the attacker-supplied URLs, with no host/scheme allowlist: [3](#0-2) .

The `DataLayer` service periodically pulls these wallet-tracked mirrors into its local subscription server list via `update_subscriptions_from_wallet()` [4](#0-3) , which is called as part of `update_subscription()` inside the periodic sync loop. Later, `fetch_and_validate()`/`insert_from_delta_file()` picks one of these server URLs and calls `http_download()`, which performs an unrestricted `aiohttp` GET to `server_info.url + "/" + filename` [5](#0-4) . There is no validation that the URL scheme is `http(s)`, nor any check against private/loopback/link-local address ranges, so the DataLayer service (the "confused deputy") will make outbound requests on the operator's behalf to any address chosen by an unrelated on-chain attacker.

This is the same bug class as GHSA-qgcg-p3v2-9h4p: an externally controlled reference (attacker-supplied mirror URL published on-chain) is used to reach a resource in another network sphere (arbitrary internal/external host) through a trusted local service acting as a deputy on behalf of the node operator.

### Impact Explanation
A single low-cost on-chain spend (creating a mirror coin, even with `amount=0`, as shown in tests) lets any user cause every DataLayer node that tracks the targeted store to issue outbound HTTP requests to attacker-chosen hosts/ports, including internal-only services, loopback interfaces, or cloud metadata endpoints reachable from the host. This can be used for internal network reconnaissance/SSRF, hitting sensitive local services (e.g. other RPC ports bound to `127.0.0.1`), or resource exhaustion by pointing many DataLayer nodes at a target simultaneously. It does not directly move coins/assets but does violate network trust boundaries and can be chained with other locally-reachable services.

### Likelihood Explanation
Likelihood is moderate-to-high: creating a mirror coin requires only a small on-chain fee/amount and no special permission — `create_new_mirror` performs no ownership check on `launcher_id`, and the mirror-coin memo format is public and simple to construct [2](#0-1) . The attack is passive for the victim: as long as their `DataLayer` service tracks/subscribes to the targeted store (common for any DataLayer store with public subscribers, since being tracked is required before the mirror is even recorded), the periodic `periodically_manage_data()` loop will automatically pick up and fetch from the malicious URL without user interaction beyond normal subscription usage.

### Recommendation
- Validate mirror URLs before storing/using them: enforce `http`/`https` scheme only, and reject or flag URLs resolving to loopback, link-local, private, and other non-routable address ranges before they are added to `get_available_servers_for_store()`'s candidate list.
- Consider making `http_download()`/`download_file()` refuse to establish TCP connections to disallowed target ranges by resolving the hostname first and validating the resolved IP (to also block DNS-rebinding to internal addresses), not just checking the string form of the URL.
- Consider requiring some indication of trust for mirrors (e.g., a per-store user-approved mirror allowlist) rather than automatically fetching from every mirror coin found on chain for any tracked launcher_id.

### Proof of Concept
1. Attacker (no special permissions) runs `chia data add_mirror` / calls `dl_new_mirror` RPC targeting a `launcher_id` for a store that victims are known to track/subscribe to, supplying `urls=["http://127.0.0.1:<victim-internal-port>/", "http://169.254.169.254/latest/meta-data/"]` and a minimal `amount`/`fee`.
2. Once the mirror coin is confirmed on chain, any victim `DataLayer` node tracking that `launcher_id` observes the coin via `DataLayerWallet.coin_added()` and records it as a mirror with the attacker's URLs (no ownership/URL validation) [1](#0-0) .
3. On the victim's next `periodically_manage_data()` cycle, `update_subscription()` calls `update_subscriptions_from_wallet()` to sync these URLs into the local subscription table, then `fetch_and_validate()`/`insert_from_delta_file()` selects the malicious server and calls `http_download()`, causing the victim's `DataLayer` process to issue an outbound HTTP GET to the attacker-chosen internal address [6](#0-5) , [5](#0-4) .

### Citations

**File:** chia/data_layer/data_layer_wallet.py (L687-704)
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

**File:** chia/data_layer/data_layer.py (L677-694)
```python
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
