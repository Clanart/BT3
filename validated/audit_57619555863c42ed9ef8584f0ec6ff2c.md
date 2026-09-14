### Title
Server-Side Request Forgery via attacker-controlled DataLayer mirror URLs - (File: `chia/data_layer/data_layer.py`)

### Summary
The DataLayer service treats mirror-coin URLs as fetch targets without any validation of scheme, host, or destination. Because mirror coins are created with a generic, ownerless puzzle (`create_mirror_puzzle()`), any unprivileged actor able to submit a spend bundle can create a `CREATE_COIN` with that puzzle hash and arbitrary attacker-chosen URL bytes memoized to a victim's tracked `launcher_id`. Any node/wallet subscribed to that DataLayer store will pick up the mirror and its `DataLayer` service will issue outbound HTTP requests to that attacker-chosen URL, constituting SSRF analogous to CVE-2021-27312 (unvalidated remote URL fetch in Gleez CMS `request.php`).

### Finding Description
`create_mirror_puzzle()` returns `P2_PARENT.curry(Program.to(1))`, a generic puzzle that is not bound to any specific owner key [1](#0-0) . `get_mirror_info()` extracts the `launcher_id` and URL list purely from the `CREATE_COIN` condition's memos with no validation of the URL content [2](#0-1) .

When a coin is created with `create_mirror_puzzle().get_tree_hash()` as its puzzle hash, `DataLayerWallet.coin_added()` treats it as a legitimate mirror for whichever `launcher_id` is embedded in the memos, and — as long as that launcher is tracked (i.e., the local node has subscribed to that store) — persists the (attacker-controlled) URLs into `dl_store` regardless of who created/funded the coin [3](#0-2) .

These persisted URLs later flow into `DataLayer.fetch_and_validate()`, which pulls `get_available_servers_for_store()` results and issues outbound requests via `insert_from_delta_file()` → `download_file()` → `http_download()`, performing an `aiohttp` GET to `server_info.url + "/" + filename` with no allow-list, scheme restriction, or private-IP/loopback blocking [4](#0-3) [5](#0-4) . The `.cursor` documentation for this module explicitly flags mirror/plugin URLs as an external trust boundary that must not be treated as verified, confirming this is a known-risky area of the code [6](#0-5) .

### Impact Explanation
A single spend bundle crafted by any unprivileged submitter can cause a victim's DataLayer node to make outbound HTTP requests to attacker-chosen hosts/ports/paths (e.g., internal RPC endpoints, cloud metadata services, or arbitrary internal infrastructure reachable from the node), on a recurring basis via the periodic `update_subscription()`/`fetch_and_validate()` loop. This is a genuine SSRF primitive reachable purely from on-chain data controlled by any spend-bundle submitter, without requiring the mirror creator to own or be associated with the store.

### Likelihood Explanation
Requires the victim to already be subscribed to (tracking) the targeted `store_id`/`launcher_id`, since `coin_added()` only records mirrors for launchers already tracked by `dl_store`. This is a normal condition for any node participating in DataLayer replication (owners, mirror consumers, or any node that has run `dl_start` / `subscribe` for a store). Because `create_mirror_puzzle()` is a shared, ownerless puzzle, no special privilege or store ownership is needed by the attacker to create a valid-looking mirror coin—only enough XCH to fund a trivial coin creation.

### Recommendation
- Validate mirror/plugin URLs before fetching: enforce an allow-list of schemes (`http`/`https`), reject loopback/link-local/private IP ranges (SSRF-style egress filtering) unless explicitly configured, and/or require DNS resolution checks at request time (not just once).
- Consider requiring mirror coins to be associated with the store's singleton owner (e.g., via an assertion binding the mirror coin creation to the launcher's inner puzzle) rather than accepting any coin with the shared mirror puzzle hash and arbitrary memos.
- Apply the same URL/host validation to `PluginRemote.url` and `proxy_url` usage in `get_downloader()`/`download_file()`.

### Proof of Concept
1. Attacker identifies a `launcher_id` (`store_id`) that the victim node is tracking (subscribed to, or as owner).
2. Attacker constructs and submits a spend bundle producing a `CREATE_COIN` with puzzle hash `create_mirror_puzzle().get_tree_hash()` and memos `[launcher_id, b"http://169.254.169.254/latest/meta-data/"]` (or any internal target URL), funded by any small amount of value the attacker controls.
3. Once confirmed, the victim's `DataLayerWallet.coin_added()` records this as a legitimate mirror for `launcher_id` [3](#0-2) .
4. On the next `periodically_manage_data()` cycle, `update_subscription()`/`fetch_and_validate()` selects this server and the victim's `DataLayer` service issues an outbound `aiohttp` GET to the attacker-chosen URL via `http_download()` [5](#0-4) , demonstrating the forged outbound request.

Note: I was not able to fully trace the exact CLVM-level constraints of `P2_PARENT`/`ACS_MU` puzzles that gate `CREATE_COIN` conditions for the mirror coin (i.e., whether any additional condition is required at spend time beyond matching the puzzle hash), so full confirmation of "anyone can spend to create this coin with arbitrary memos" would benefit from a deeper CLVM trace of `P2_PARENT` in a live/dev environment.

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

**File:** chia/data_layer/download_data.py (L298-323)
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
```

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
