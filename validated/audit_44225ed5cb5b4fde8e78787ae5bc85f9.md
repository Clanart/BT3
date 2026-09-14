### Title
Server-Side Request Forgery via unvalidated DataLayer mirror URLs fetched by any subscribing node - (File: `chia/data_layer/download_data.py`)

### Summary
Chia's DataLayer allows any wallet owner to publish a "mirror" coin on-chain that carries an arbitrary URL in its memos. Any full node/wallet running the DataLayer service that tracks the corresponding store (launcher) will later read that URL out of chain data and cause its own DataLayer service to issue outbound `aiohttp` GET requests to it, with no validation of scheme, host, or IP range — the same bug class as the memos `/o/get/httpmeta` SSRF (GHSA-6fcf-g3mp-xj2x), but here the "attacker-controlled URL" arrives via chain data instead of a direct HTTP parameter.

### Finding Description
1. `DataLayerWallet.create_new_mirror()` creates a coin to `create_mirror_puzzle()` whose memos are `[launcher_id, *urls]`, with no restriction on the URL content: [1](#0-0) 

2. When that coin is confirmed, `DataLayerWallet.coin_added()` decodes the memos via `get_mirror_info()` and stores the URLs as a tracked `Mirror` for the launcher id, again with no URL/scheme validation: [2](#0-1) [3](#0-2) 

3. Any node that already tracks that `launcher_id` (any peer that previously ran `dl_track_new`, e.g. an offer counterparty or someone who simply subscribed to the public store) picks up these mirrors automatically through `update_subscriptions_from_wallet()`, which copies `mirror.urls` straight into subscription `ServerInfo.url` values: [4](#0-3) 

4. During the periodic sync loop, `fetch_and_validate()` iterates these `servers_info` and calls `insert_from_delta_file()`/`http_download()`, which performs an unauthenticated outbound HTTP GET built directly from the attacker-supplied URL with no allow-list, scheme check, or private-IP filtering: [5](#0-4) [6](#0-5) 

There is no validation anywhere in this path that the URL scheme is `http`/`https` (as opposed to `file://`, or that the host isn't a loopback/link-local/internal address like `169.254.169.254` or `127.0.0.1:<internal-port>`). The only "attacker cost" is publishing a coin with `amount` mojos (can be very low) plus a fee — well within reach of any wallet holder, i.e. an unprivileged spend-bundle submitter/wallet user.

### Impact Explanation
This lets a low-privilege wallet user cause any other node that tracks/subscribes to the same DataLayer store to make outbound HTTP requests to attacker-chosen internal or link-local addresses (e.g. cloud metadata endpoints, internal admin panels, other services on localhost). Because response handling differs based on connection success, HTTP status, and response size/behavior (`resp.raise_for_status()`, content-length checks, timeouts), it enables blind network reconnaissance/enumeration of the victim node's internal network — the exact bug class described in the memos advisory, though the response body itself is not directly returned to the attacker in Chia (no direct data exfiltration channel was found), limiting impact primarily to internal network mapping/probing and triggering requests against internal services (potential further SSRF-driven side effects depending on what's reachable).

### Likelihood Explanation
Likelihood is real but bounded: the victim's DataLayer service must already be tracking/subscribed to the specific store id (via `dl_track_new`/subscription), which typically happens through legitimate interaction such as accepting a DL offer, browsing/mirroring a shared store, or being a DataLayer participant. Given that data stores and their mirrors are intentionally discoverable/public (that's the point of "mirrors"), and creating a mirror coin only requires a wallet spend (no special permission), the precondition is easy to satisfy for any store the attacker can get others to track.

### Recommendation
- Validate mirror URLs when they are ingested (in `DataLayerWallet.coin_added()` / `Mirror.decode_urls`) and again at fetch time in `download_data.http_download()`/`data_layer.fetch_and_validate()`: enforce `http`/`https` scheme only, and reject/deny resolution to loopback, link-local, private, and other non-routable IP ranges (defense against DNS rebinding as well).
- Consider requiring operator opt-in / an explicit allow-list for mirror hosts before the DataLayer server will automatically fetch from them, similar to hardening done for other SSRF-prone metadata-fetch features.
- Add tests asserting that mirrors pointing at private/loopback/link-local addresses are rejected or never dialed.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (RPC) or `chia data add_mirror` with `launcher_id` = an existing/public DataLayer store id and `urls=["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:<victim-internal-port>/"]`, paying a small `amount`/`fee`: [7](#0-6) 
2. The mirror coin confirms on chain; any victim node already tracking that `launcher_id` observes the coin via `coin_added()` and stores the malicious URLs as a `Mirror`: [2](#0-1) 
3. On its next `periodically_manage_data()` cycle, the victim's `update_subscriptions_from_wallet()` copies these URLs into its subscription `ServerInfo`, and `fetch_and_validate()` → `insert_from_delta_file()` → `http_download()` issue an outbound `aiohttp.ClientSession().get(...)` to the attacker-chosen internal address: [6](#0-5) 
4. Observing timing/log differences (`server_misses_file`, ban/backoff behavior in `_tests/core/data_layer/test_data_store.py::test_server_http_ban`) lets the attacker infer whether the internal target is reachable, effectively enumerating the victim's internal network — the SSRF bug class matching CVE-2024-29028. [8](#0-7)

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

**File:** chia/data_layer/data_layer.py (L642-666)
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

**File:** chia/_tests/core/data_layer/test_data_store.py (L1350-1402)
```python
@pytest.mark.parametrize(
    "error",
    [True, False],
)
@pytest.mark.anyio
async def test_server_http_ban(
    data_store: DataStore,
    store_id: bytes32,
    error: bool,
    monkeypatch: Any,
    tmp_path: Path,
    seeded_random: random.Random,
) -> None:
    sinfo = ServerInfo("http://127.0.0.1/8003", 0, 0)
    await data_store.subscribe(Subscription(store_id, [sinfo]))

    async def mock_http_download(
        target_filename_path: Path,
        filename: str,
        proxy_url: str | None,
        server_info: ServerInfo,
        timeout: aiohttp.ClientTimeout,
        log: logging.Logger,
        max_delta_file_size: int,
    ) -> None:
        if error:
            raise aiohttp.ClientConnectionError

    frozen_time = time.time()
    start_timestamp = int(frozen_time)
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: frozen_time)
        m.setattr("chia.data_layer.download_data.http_download", mock_http_download)
        success = await insert_from_delta_file(
            data_store=data_store,
            store_id=store_id,
            existing_generation=3,
            target_generation=4,
            root_hashes=[bytes32.random(seeded_random)],
            server_info=sinfo,
            client_foldername=tmp_path,
            timeout=aiohttp.ClientTimeout(total=15, sock_connect=5),
            log=log,
            proxy_url="",
            downloader=None,
        )

    assert success is False

    subscriptions = await data_store.get_subscriptions()
    sinfo = subscriptions[0].servers_info[0]
    assert sinfo.num_consecutive_failures == 1
    assert sinfo.ignore_till == start_timestamp + 5 * 60  # ban for 5 minutes
```
