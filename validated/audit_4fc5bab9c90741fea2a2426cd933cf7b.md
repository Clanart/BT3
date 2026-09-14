Confirmed: `create_new_mirror()` uses `create_mirror_puzzle()` = `P2_PARENT.curry(Program.to(1))`, which is a fixed, permissionless puzzle hash any spender can create a coin against, curried with an arbitrary `launcher_id` and arbitrary URL bytes as memos.### Title
DataLayer mirror URLs are trusted from any on-chain coin, letting an unauthenticated coin creator trigger SSRF-style forced HTTP fetches by DataLayer nodes - ([File: chia/data_layer/data_layer_wallet.py])

### Summary
Any party can spend to create a coin with the fixed, permissionless `create_mirror_puzzle()` puzzle hash, curried/memoed with an arbitrary `launcher_id` (any DataLayer store id) and arbitrary URL strings. `DataLayerWallet.coin_added()` accepts this coin's URLs into the local mirror table for *any* tracked launcher id with no check on who created the coin. The DataLayer service (`chia/data_layer/data_layer.py`) then pulls these mirror URLs via `update_subscriptions_from_wallet()` and automatically issues outbound HTTP requests to them from `fetch_and_validate()`/`download_file()`/`http_download()` in `chia/data_layer/download_data.py`, all without any authentication of the URL's origin. This mirrors the CVE's core pattern: attacker-supplied, embedded URL references are resolved server-side without validating trust/origin, causing the resolving service to make outbound requests to attacker-chosen destinations (SSRF).

### Finding Description
- `create_mirror_puzzle()` = `P2_PARENT.curry(Program.to(1))` is a fixed puzzle anyone can pay to create a coin against: [1](#0-0) 
- `create_new_mirror()` builds the coin with the target `launcher_id` and URL bytes as memos, with no restriction that the launcher_id be one the spender owns: [2](#0-1) 
- On any node syncing chain state, `coin_added()` matches the fixed mirror puzzle hash, decodes `launcher_id, urls` from the parent spend via `get_mirror_info()`, and — as long as the `launcher_id` is being tracked locally (e.g., because the node subscribes to that public DataLayer store) — inserts the URLs into the wallet's mirror table. The only "ours" check just tags metadata; it does not gate insertion: [3](#0-2) 
- `get_mirror_info()` simply extracts `CREATE_COIN` condition memos matching the mirror puzzle hash — it performs no validation of URL scheme/content or spender identity: [4](#0-3) 
- The DataLayer service periodically calls `update_subscriptions_from_wallet()`, which fetches all mirror URLs from the wallet (`dl_get_mirrors`) for a subscribed store id and merges them into the local subscription/server list unconditionally: [5](#0-4) [6](#0-5) 
- `fetch_and_validate()` then randomly selects one of these server URLs and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which issues an `aiohttp` GET request to `server_info.url + "/" + filename` — a URL fully controlled by whoever created the mirror coin: [7](#0-6) [8](#0-7) 

The attack requires no wallet ownership relationship to the target store: only that the attacker's spend bundle is accepted into a block (any small fee/amount) and that a victim DataLayer node happens to be tracking/subscribed to the referenced `launcher_id` — which is the normal operating mode for any DataLayer node that mirrors/subscribes to a publicly known store.

### Impact Explanation
This allows an unprivileged, unauthenticated party (anyone able to submit a spend bundle) to force arbitrary DataLayer nodes that subscribe to a given store to make outbound HTTP requests to attacker-chosen destinations — the classic SSRF impact pattern from the CVE. Reachable internal targets could include internal-only HTTP services, cloud metadata endpoints, or other DataLayer/plugin infrastructure reachable from the node's network position. This does not directly cause coin-set divergence or asset forgery, but it is a genuine SSRF vector reachable purely through an on-chain, permissionless coin creation (equivalent to "spend-triggered" behavior), consistent with the required severity band (SSRF/Medium).

### Likelihood Explanation
Likelihood is moderate-to-high for any operator running a DataLayer node with subscriptions to shared/public stores: mirror coins are cheap to create (arbitrary `amount`/`fee`), the puzzle hash is fixed and public, and no authorization step exists between "coin observed on chain" and "URL trusted as a mirror/subscription server." The main precondition is that the victim node must be tracking the targeted `launcher_id`, which is the default state for any node actively syncing/mirroring that store.

### Recommendation
- Do not treat mirror-coin URLs from arbitrary, unauthenticated spends as trusted download targets; require some binding to store ownership/administration (e.g., only accept mirrors created by the store's own DataLayer wallet, or introduce an allow-list/verification step) before merging them into `update_subscriptions_from_wallet()`/subscription server lists.
- Constrain outbound fetch destinations (e.g., disallow private/internal IP ranges, cloud metadata addresses, non-http(s) schemes) in `http_download()` and downloader plugin dispatch, similar to standard SSRF mitigations.
- Consider rate-limiting/backoff and stricter validation of mirror URL format before they are ever attempted for download.

### Proof of Concept
1. Attacker crafts and broadcasts a spend bundle that creates a coin with puzzle hash `create_mirror_puzzle().get_tree_hash()`, with `CREATE_COIN` memos `[launcher_id, url1, url2, ...]` where `launcher_id` is the store id of a well-known/public DataLayer store the victim subscribes to, and `url1` points to an attacker-controlled or internal-network address (e.g., `http://169.254.169.254/...` or an internal service port).
2. The spend is accepted into a block like any ordinary coin creation (no special privileges needed) — see `create_new_mirror()`/`create_mirror_puzzle()`: [2](#0-1) 
3. Any full/wallet node tracking that `launcher_id` observes the coin and stores the attacker URL as a mirror in `coin_added()`: [3](#0-2) 
4. The victim's DataLayer service periodically calls `update_subscriptions_from_wallet()` and `fetch_and_validate()`, which will attempt `http_download()` against the attacker's URL, causing the node to issue outbound requests to the attacker-chosen destination: [8](#0-7)

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L90-94)
```python
def create_mirror_puzzle() -> Program:
    return P2_PARENT.curry(Program.to(1))


MIRROR_PUZZLE_HASH = create_mirror_puzzle().get_tree_hash()
```

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L97-110)
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
    raise ValueError("The provided puzzle and solution do not create a mirror coin")
```

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

**File:** chia/data_layer/data_store.py (L1668-1704)
```python
    async def update_subscriptions_from_wallet(self, store_id: bytes32, new_urls: list[str]) -> None:
        async with self.db_wrapper.writer() as writer:
            cursor = await writer.execute(
                "SELECT * FROM subscriptions WHERE from_wallet == 1 AND tree_id == :tree_id",
                {
                    "tree_id": store_id,
                },
            )
            old_urls = [row["url"] async for row in cursor]
            cursor = await writer.execute(
                "SELECT * FROM subscriptions WHERE from_wallet == 0 AND tree_id == :tree_id",
                {
                    "tree_id": store_id,
                },
            )
            from_subscriptions_urls = {row["url"] async for row in cursor}
            additions = {url for url in new_urls if url not in old_urls}
            removals = [url for url in old_urls if url not in new_urls]
            for url in removals:
                await writer.execute(
                    "DELETE FROM subscriptions WHERE url == :url AND tree_id == :tree_id",
                    {
                        "url": url,
                        "tree_id": store_id,
                    },
                )
            for url in additions:
                if url not in from_subscriptions_urls:
                    await writer.execute(
                        "INSERT INTO subscriptions(tree_id, url, ignore_till, num_consecutive_failures, from_wallet) "
                        "VALUES (:tree_id, :url, 0, 0, 1)",
                        {
                            "tree_id": store_id,
                            "url": url,
                        },
                    )

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
