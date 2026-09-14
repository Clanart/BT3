### Title
DataLayer mirror URLs from unauthenticated on-chain spends drive unchecked outbound HTTP fetches (SSRF) - ([File: chia/data_layer/download_data.py])

### Summary
Any coin spender can create a coin at the fixed `create_mirror_puzzle()` puzzle hash with memos containing an arbitrary "launcher_id" and a list of arbitrary URL strings. Wallets tracking that launcher parse the spend and record these attacker-supplied URLs as a `Mirror`. The DataLayer service then treats these URLs as legitimate file-serving servers and performs unauthenticated `aiohttp` HTTP GET/POST requests against them with no restriction on target host/IP, mirroring the SSRF class described in the OpenClaw advisory (unchecked `fetch()` to attacker-controlled targets in cron webhook delivery).

### Finding Description
`get_mirror_info()` in `chia/wallet/db_wallet/db_wallet_puzzles.py` (lines 97-109) runs any coin spend's puzzle/solution and extracts a `CREATE_COIN` condition whose puzzle hash equals `create_mirror_puzzle().get_tree_hash()` — a fixed, publicly known puzzle (`P2_PARENT.curry(1)`). It pulls the `launcher_id` and an arbitrary list of URL bytes straight from the memos with no validation of URL format, scheme, or target: [1](#0-0) 

`DataLayerWallet.coin_added()` in `chia/data_layer/data_layer_wallet.py` processes *any* coin observed on chain matching the mirror puzzle hash (not restricted to coins the wallet created or owns), and if the referenced `launcher_id` happens to be a store the wallet is tracking, stores the attacker-controlled URLs as a `Mirror` record: [2](#0-1) 

`DataLayer.update_subscriptions_from_wallet()` pulls these mirror URLs from the wallet and feeds them directly into the local `DataStore` as download server candidates, with no filtering of private/link-local/internal addresses: [3](#0-2) 

`DataLayer.fetch_and_validate()` then selects one of these attacker-controlled server URLs and calls `insert_from_delta_file()` → `download_file()`, which performs an unauthenticated HTTP request (`http_download`) or a plugin POST to the raw URL: [4](#0-3) [5](#0-4) 

No code path validates that the mirror URL resolves to a public, non-internal address (no blocklist for loopback, link-local metadata endpoints such as `169.254.169.254`, RFC1918 ranges, or other internal services) before the DataLayer service or plugin issues the outbound request.

### Impact Explanation
Any party able to broadcast a spend bundle (no special privileges, no wallet keys tied to the target store) can force a full node/DataLayer service that is *subscribed to or hosting* the targeted store to issue outbound HTTP requests to attacker-chosen internal/private endpoints (cloud metadata services, internal RPC ports, other local services on the operator's network). This is a spend-bundle-triggered SSRF against any node tracking DataLayer mirrors for a given store, which can be used to probe/reach internal infrastructure, exfiltrate data via response timing/side channels, or interact with internal services that trust localhost/internal-network callers. It does not directly cause coin/asset forgery, but it is a concrete unauthorized network-reachability primitive triggered entirely by an untrusted, unprivileged on-chain spend — matching the CWE-918/SSRF class of the referenced advisory.

### Likelihood Explanation
Likelihood is moderate: it requires (a) the target node to be running the DataLayer service and (b) tracking/subscribed to the specific store referenced by `launcher_id`. Any user (not just the store owner) can create the mirror coin cheaply since the mirror puzzle hash is fixed and public, and mirror processing/subscription-URL propagation happens automatically without operator confirmation once a store is tracked.

### Recommendation
Validate and sanitize mirror URLs before treating them as download targets: reject non-HTTP(S) schemes handled outside of configured plugins, resolve and block private/loopback/link-local/multicast IP ranges (RFC1918, 127.0.0.0/8, 169.254.0.0/16, ::1, fc00::/7, etc.) before `http_download`/plugin POST calls in `chia/data_layer/download_data.py` and `chia/data_layer/data_layer.py`, and consider requiring explicit operator allow-listing of mirror URL hosts rather than trusting attacker-supplied on-chain memos automatically.

### Proof of Concept
1. Attacker crafts a spend bundle that creates a coin with puzzle hash `create_mirror_puzzle().get_tree_hash()` and a `CREATE_COIN` condition whose memo list is `[launcher_id_of_victim_store, b"http://169.254.169.254/latest/meta-data/", ...]` and broadcasts it (any coin/amount can fund this coin; no special key needed for the target `launcher_id`).
2. A full node running the DataLayer service that tracks `launcher_id_of_victim_store` (e.g., subscribed as a DL client) picks up the coin via `coin_added()` in `chia/data_layer/data_layer_wallet.py`, decodes the URL from memos via `get_mirror_info()`, and stores it as a `Mirror`.
3. On the next `periodically_manage_data()` cycle, `update_subscriptions_from_wallet()` copies the URL into `DataStore` subscription server list, and `fetch_and_validate()`/`download_file()` issue an outbound HTTP request to `http://169.254.169.254/latest/meta-data/` (or any other internal target) with no host validation, confirming the SSRF.

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L97-109)
```python
def get_mirror_info(
    parent_puzzle: Program | SerializedProgram, parent_solution: Program | SerializedProgram
) -> tuple[bytes32, list[bytes]]:
    assert type(parent_puzzle) is type(parent_solution)
    _, conditions = run_with_cost(parent_puzzle, INFINITE_COST, parent_solution)
    for condition in conditions.as_iter():
        if (
            condition.first().as_python() == ConditionOpcode.CREATE_COIN
            and condition.at("rf").as_python() == create_mirror_puzzle().get_tree_hash()
        ):
            memos: list[bytes] = condition.at("rrrf").as_python()
            launcher_id = bytes32(memos[0])
            return launcher_id, [url for url in memos[1:]]
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
