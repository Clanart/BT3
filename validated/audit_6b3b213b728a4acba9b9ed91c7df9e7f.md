## Analysis Result

I found a concrete analog. The GitLab bug class (an unprivileged actor abusing a "webhook"/callback feature that fetches a client-supplied URL, causing a denial-of-service) maps to the Data Layer **mirror** feature: any wallet user can create an on-chain "mirror" coin whose memos encode an arbitrary `launcher_id` and a list of arbitrary URL strings, and **every** Data Layer node/wallet that is tracking that `launcher_id` will automatically ingest that mirror and later fetch content from the attacker-controlled URLs.

### Title
Unauthenticated mirror-coin URL injection lets any wallet abuse Data Layer HTTP client (mirror/webhook-style DoS) - ([File: chia/data_layer/data_layer_wallet.py])

### Summary
The `create_mirror_puzzle()` (`P2_PARENT.curry(Program.to(1))`) is a fixed, "anyone can spend"-style puzzle whose tree hash is `MIRROR_PUZZLE_HASH` [1](#0-0) . Any unprivileged spend-bundle submitter can create a coin with that puzzle hash and attach memos `[launcher_id, url1, url2, ...]` — there is no check that the sender owns or controls the `launcher_id`, and no validation on the URL contents. `DataLayerWallet.coin_added` decodes these memos via `get_mirror_info` and, if the `launcher_id` is one the local node is tracking, blindly stores the attacker-chosen URLs as a legitimate `Mirror` record [2](#0-1) , [3](#0-2) .

### Finding Description
`DataLayer.update_subscriptions_from_wallet` later reads all mirrors for a store and feeds their URLs directly into the subscription/server-info list used for syncing store data [4](#0-3) . Those URLs are subsequently used to attempt downloads via `download_file`/`http_download` in `chia/data_layer/download_data.py`, or via a plugin `downloader.url + "/download"` POST request [5](#0-4) . Because the mirror-coin creation path performs **no authorization check that the creator controls the launcher/store** — the only requirement is that some local wallet is tracking that `launcher_id` (`is_launcher_tracked`) — any third party who knows (or guesses) a public store's `launcher_id` can inject arbitrary URLs that will be fetched by every peer's Data Layer node syncing that store. This is directly analogous to the GitLab webhook DoS pattern: a feature that lets an unprivileged party register a URL that the server later fetches automatically, which can be pointed at slow-loris endpoints, internal/loopback addresses, gzip/decompression bombs, or endlessly redirecting hosts to consume node resources or hang Data Layer sync.

### Impact Explanation
This does not cause supply inflation, unauthorized coin movement, or invalid block/spend acceptance — it is a resource-exhaustion/availability issue restricted to nodes running the Data Layer service and actively tracking the targeted store. It can degrade or hang a targeted node's Data Layer sync loop (mirrors fetched sequentially per store), and if a malicious downloader plugin URL is reachable, could also be used for SSRF against internal endpoints from the node's own network. Given the strict validation rule that only concrete coin-set divergence, forged identity, theft, or a spend-triggered transaction-processing halt counts, this analog is best characterized as **Medium** severity: it is a spend-bundle-triggered denial-of-service against Data Layer clients tracking the affected store, not a full transaction-processing halt for the whole node.

### Likelihood Explanation
Likelihood is high for anyone who knows a target store's `launcher_id` (these are public, since Data Layer store IDs/roots are typically shared for subscription purposes). Creating a mirror coin costs only a fee and requires no special permission — `dl_new_mirror`/`add_mirror` RPCs and even a plain spend bundle constructing a `create_mirror_puzzle()` coin with the right memos will work, since the on-chain enforcement is only that the coin's puzzle hash matches; ownership of the launcher is never checked.

### Recommendation
Add validation before trusting a mirror record: verify that the mirror-coin creator has some verifiable relationship to the launcher (e.g., require the creator to also own/have created the singleton, or otherwise cryptographically authorize mirror URLs tied to the launcher), rate-limit and sandbox outbound mirror fetches (enforce strict timeouts, response size caps, SSRF protections such as blocking private/loopback address ranges, and refusing redirects to disallowed hosts), and treat all mirror URLs as fully untrusted network input in `download_data.py`.

### Proof of Concept
1. Attacker learns the `launcher_id` of a Data Layer store that victims are tracking (store IDs are meant to be shared publicly for subscription).
2. Attacker submits a spend bundle (or uses `dl_new_mirror`/`add_mirror` RPC with a minimal-fee wallet, no relationship to the store required) creating a coin with puzzle hash `create_mirror_puzzle().get_tree_hash()` and memos `[launcher_id, b"http://attacker-controlled-slow-or-malicious-endpoint"]`, per `DLNewMirror`/`create_new_mirror` [6](#0-5) .
3. Once confirmed, every victim node tracking `launcher_id` picks it up in `coin_added` and stores it as a legitimate `Mirror`, with no verification against launcher ownership [2](#0-1) .
4. Victim's Data Layer service later calls `update_subscriptions_from_wallet` and `download_file`, causing it to fetch from the attacker-controlled URL, which can hang, redirect endlessly, or return oversized content to consume victim resources [4](#0-3) , [7](#0-6) .

**Uncertainty note:** I could not fully verify what size/timeout limits `http_download` enforces beyond `max_delta_file_size` and the `MaxDeltaFileSizeExceededError` check referenced in `download_file`, since the full body of `http_download` wasn't retrieved in the index results. That function's exact DoS resilience (timeout handling, redirect limits) should be reviewed directly in the repository for a complete assessment.

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
