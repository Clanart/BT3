### Title
DataLayer mirror-coin subscription mechanism causes blind SSRF via arbitrary attacker-supplied URLs - (File: `chia/data_layer/data_layer_wallet.py`, `chia/data_layer/download_data.py`)

### Summary
`create_mirror_puzzle()` is a globally-known, unparameterized-by-owner puzzle (`P2_PARENT.curry(Program.to(1))`, `MIRROR_PUZZLE_HASH`) [1](#0-0) . Any unprivileged wallet user can create a coin sent to this puzzle hash with memos `[launcher_id, url1, url2, ...]` for **any** DataLayer store id they choose — they do not need to own or control that singleton. Any node/wallet that is tracking that `launcher_id` will automatically ingest these attacker-chosen URLs into its mirror table via `coin_added()` [2](#0-1) , and the DataLayer service will subsequently make outbound HTTP GET/POST requests to those URLs with zero validation of host/IP/port [3](#0-2) [4](#0-3) .

### Finding Description
The mirror-creation flow is:
1. `DataLayerWallet.create_new_mirror()` builds a standard coin spend paying to `create_mirror_puzzle().get_tree_hash()` with memos `[launcher_id, *urls]` — there is no check that the caller owns `launcher_id`, and no validation of the URL strings (scheme, host, port) [5](#0-4) .
2. On the receiving side, `DataLayerWallet.coin_added()` recognizes any coin sent to the mirror puzzle hash, decodes `launcher_id, urls` from the parent spend via `get_mirror_info()`, and — as long as the node is tracking that `launcher_id` (`is_launcher_tracked`) — persists the mirror record regardless of who created it (`ours` is just a local flag; the record itself is added unconditionally) [2](#0-1) .
3. `DataLayer.update_subscriptions_from_wallet()` pulls all mirror URLs for a store id from wallet RPC and pushes them into `DataStore.update_subscriptions_from_wallet()`, which persists them as subscription server URLs without validation [3](#0-2) [6](#0-5) .
4. The periodic sync loop (`update_subscription()` → `fetch_and_validate()`) then issues outbound HTTP requests to these URLs via `http_download()`, which does a bare `aiohttp` GET to `server_info.url + "/" + filename` with no host/IP allow-listing (no rejection of loopback, link-local, RFC1918 private ranges, cloud metadata IPs like `169.254.169.254`, etc.) [4](#0-3) . Additionally `get_downloader()` performs an unrestricted `session.post` to plugin URLs sourced from local config, and `download_file()`'s plugin path POSTs the mirror URL to the downloader plugin as a parameter [7](#0-6)  — but the core SSRF vector is the direct `http_download` GET against attacker-controlled `server_info.url`.

Any spend-bundle submitter can therefore cause the DataLayer service on **any node subscribed to a given store** (a widely-shared, publicly known `launcher_id`) to issue outbound requests to attacker-chosen internal hosts/ports, purely by broadcasting a spend bundle that creates a mirror coin with `launcher_id` set to a store the target subscribes to, entirely mirroring the HomeBox notifier SSRF pattern: user-supplied URL destinations, no validation, and behavior (success/failure/backoff via `received_correct_file`/`server_misses_file`/`ignore_till`) that differs based on network reachability of the target, creating an observable side-channel for internal service enumeration [8](#0-7) .

### Impact Explanation
This lets an unprivileged spend-bundle submitter cause an arbitrary Chia full-node/DataLayer service (any node subscribed to the targeted store) to make outbound TCP/HTTP requests to attacker-chosen internal addresses/ports. This can be used to fingerprint internal network topology (open ports, service banners via HTTP response codes/timing), and potentially to reach internal-only management endpoints reachable from the node's network position. It does not directly cause coin-theft, inflation, or invalid state, so severity is capped at Medium, matching the referenced CVE-2026-27600 classification (CVSS 5.0).

### Likelihood Explanation
Likelihood is high in terms of reachability: submitting a spend creating a mirror coin with attacker-chosen `launcher_id`/URLs requires no special privilege beyond funds for a transaction, and any node that already subscribes to that `launcher_id`/store (a value discoverable/public for shared DataLayer stores) will process it automatically in its periodic subscription loop with no operator interaction. The URL fields carry no validation anywhere along the pipeline (`create_new_mirror` → `coin_added` → `update_subscriptions_from_wallet` → `http_download`).

### Recommendation
- Validate mirror/subscription URLs before persisting or fetching them: enforce scheme allow-list (http/https), reject `localhost`/loopback/link-local/private RFC1918/RFC4193 ranges and cloud metadata addresses unless explicitly configured to allow them (e.g., for local testing).
- Consider requiring that only mirrors created by the store owner (or an explicit local allow-list) are trusted for automatic fetching, rather than blindly trusting any `CREATE_COIN` to the mirror puzzle hash referencing a tracked `launcher_id`.
- Apply the same URL validation to `get_downloader()`/plugin URLs sourced from mirrors.
- Normalize error handling so that connectivity failures are not distinguishable in timing/logging from generic errors, reducing the internal-enumeration side channel.

### Proof of Concept
Not independently executed; the following spend/RPC sequence is derivable from the code paths cited above:
1. Attacker crafts a standard spend that creates a coin with puzzle hash `MIRROR_PUZZLE_HASH` (from `create_mirror_puzzle()`), memos `[launcher_id_of_victim_store, b"http://169.254.169.254/", b"http://10.0.0.5:6379/"]`, per the shape produced by `create_new_mirror()` [5](#0-4)  — this can be built without going through `create_new_mirror`, directly with a standard-wallet spend to that puzzle hash and those memos.
2. Attacker broadcasts the spend bundle to the mempool; it needs no relationship to the actual DataLayer singleton owner.
3. Any node subscribed to `launcher_id_of_victim_store` sees the coin via `coin_added()`, records the mirror (`is_launcher_tracked` is true because it subscribes to that store) [2](#0-1) .
4. On its next `periodically_manage_data()` cycle, `update_subscriptions_from_wallet()`/`fetch_and_validate()` cause `http_download()` to issue outbound GET requests to the attacker-chosen internal addresses [3](#0-2) [4](#0-3) , with success/failure state observable via `get_mirrors`/`get_subscriptions` RPC responses (`ignore_till`, `num_consecutive_failures`) as a blind side-channel [8](#0-7) .

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L90-94)
```python
def create_mirror_puzzle() -> Program:
    return P2_PARENT.curry(Program.to(1))


MIRROR_PUZZLE_HASH = create_mirror_puzzle().get_tree_hash()
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

**File:** chia/data_layer/download_data.py (L146-168)
```python

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

**File:** chia/data_layer/data_store.py (L1668-1703)
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

**File:** chia/_tests/core/data_layer/test_data_store.py (L1289-1347)
```python
@pytest.mark.anyio
async def test_server_selection(data_store: DataStore, store_id: bytes32) -> None:
    start_timestamp = 1000
    await data_store.subscribe(
        Subscription(store_id, [ServerInfo(f"http://127.0.0.1/{port}", 0, 0) for port in range(8000, 8010)])
    )

    free_servers = {f"http://127.0.0.1/{port}" for port in range(8000, 8010)}
    tried_servers = 0
    random = Random()
    random.seed(100, version=2)
    while len(free_servers) > 0:
        servers_info = await data_store.get_available_servers_for_store(store_id=store_id, timestamp=start_timestamp)
        random.shuffle(servers_info)
        assert servers_info != []
        server_info = servers_info[0]
        assert server_info.ignore_till == 0
        await data_store.received_incorrect_file(store_id=store_id, server_info=server_info, timestamp=start_timestamp)
        assert server_info.url in free_servers
        tried_servers += 1
        free_servers.remove(server_info.url)

    assert tried_servers == 10
    servers_info = await data_store.get_available_servers_for_store(store_id=store_id, timestamp=start_timestamp)
    assert servers_info == []

    current_timestamp = 2000 + 7 * 24 * 3600
    selected_servers = set()
    for _ in range(100):
        servers_info = await data_store.get_available_servers_for_store(store_id=store_id, timestamp=current_timestamp)
        random.shuffle(servers_info)
        assert servers_info != []
        selected_servers.add(servers_info[0].url)
    assert selected_servers == {f"http://127.0.0.1/{port}" for port in range(8000, 8010)}

    for _ in range(100):
        servers_info = await data_store.get_available_servers_for_store(store_id=store_id, timestamp=current_timestamp)
        random.shuffle(servers_info)
        assert servers_info != []
        if servers_info[0].url != "http://127.0.0.1/8000":
            await data_store.received_incorrect_file(
                store_id=store_id, server_info=servers_info[0], timestamp=current_timestamp
            )

    servers_info = await data_store.get_available_servers_for_store(store_id=store_id, timestamp=current_timestamp)
    random.shuffle(servers_info)
    assert len(servers_info) == 1
    assert servers_info[0].url == "http://127.0.0.1/8000"
    await data_store.received_correct_file(store_id=store_id, server_info=servers_info[0])

    ban_times = [5 * 60] * 3 + [15 * 60] * 3 + [30 * 60] * 2 + [60 * 60] * 10
    for ban_time in ban_times:
        servers_info = await data_store.get_available_servers_for_store(store_id=store_id, timestamp=current_timestamp)
        assert len(servers_info) == 1
        await data_store.server_misses_file(store_id=store_id, server_info=servers_info[0], timestamp=current_timestamp)
        current_timestamp += ban_time
        servers_info = await data_store.get_available_servers_for_store(store_id=store_id, timestamp=current_timestamp)
        assert servers_info == []
        current_timestamp += 1
```
