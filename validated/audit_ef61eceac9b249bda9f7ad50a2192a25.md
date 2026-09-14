## Title
DataLayer mirror/subscription URLs allow an unprivileged on-chain spend to make a DataLayer node issue attacker-directed outbound HTTP requests (SSRF/DoS analog to CVE-2022-24280) - (File: `chia/data_layer/data_layer.py`, `chia/data_layer/download_data.py`, `chia/data_layer/data_layer_wallet.py`)

### Summary
Any party who can broadcast a spend bundle can create a DataLayer "mirror" coin carrying attacker-chosen URLs for an arbitrary (even someone else's) store/launcher id, with no restriction on host, port, or scheme, and no minimum coin amount. Every DataLayer node that is subscribed to (or owns/tracks) that store id will periodically fetch those URLs from wallet state and issue outbound HTTP requests to them without validating that the target is a legitimate, safe destination. This mirrors the structure of CVE-2022-24280 (Apache Pulsar Proxy): a low-privilege, authenticated actor supplies a network destination that is not properly validated, and the target service becomes an origin for arbitrary outbound TCP/HTTP connection attempts, usable for DoS or as an internal-network probing/amplification vector.

### Finding Description
The mirror puzzle used for publishing DataLayer mirror URLs is a fully public "anyone can spend to" style puzzle: [1](#0-0) 

`create_new_mirror()` lets any wallet create a coin to this puzzle for any `launcher_id`, with attacker-controlled `urls` and even a zero fee/amount, as demonstrated in tests: [2](#0-1) [3](#0-2) 

There is no on-chain or wallet-side check that the mirror creator owns or controls the referenced `launcher_id`/store — `coin_added()` accepts and records the mirror as long as the launcher id is tracked at all: [4](#0-3) 

The DataLayer service periodically pulls these attacker-supplied URLs from wallet mirror records and merges them into its subscription server list, without any URL/host/scheme validation: [5](#0-4) 

Those URLs are then used directly to issue outbound HTTP GET requests on a periodic control loop (`manage_data_interval`, default 60s) via `fetch_and_validate()` → `insert_from_delta_file()` → `download_file()` → `http_download()`: [6](#0-5) [7](#0-6) 

`http_download()` builds the request target as `server_info.url + "/" + filename` and performs `session.get(...)` with no restriction on scheme, host, or destination IP (no blocking of loopback/link-local/internal ranges, no allow-list). The `RPC` endpoints `add_mirror`/`subscribe` similarly pass through user-controlled URLs with no validation: [8](#0-7) [9](#0-8) 

### Impact Explanation
Any user who can broadcast a spend bundle (near-zero cost, a single `CREATE_COIN` to the public mirror puzzle) can cause every DataLayer node that later tracks/subscribes to that store id to repeatedly originate outbound HTTP requests toward an attacker-chosen destination on every sync cycle, indefinitely (until the mirror is deleted/expired). This can be used to: (a) direct many independent DataLayer nodes' outbound traffic at a third-party victim host/port as a DoS amplification vector originating from many different node IPs (mirroring the Pulsar Proxy DoS abuse pattern), or (b) degrade/DoS the local DataLayer node itself by pointing at slow-responding or hung endpoints, tying up the bounded worker pool (`subscription_update_concurrency`) used by `periodically_manage_data()`. This is reachable purely from an unprivileged, minimally-funded spend bundle — no privileged access or malicious peer/node role required.

### Likelihood Explanation
Likelihood is high for any node that subscribes to third-party DataLayer stores (a normal, expected usage pattern for DataLayer clients) since mirror creation requires only a trivial spend, no ownership check exists, and the resulting URLs are pulled and dereferenced automatically by background sync with no operator interaction. The blast radius scales with however many independent nodes track/subscribe to the targeted store id.

### Recommendation
- Enforce URL scheme/host validation before storing or fetching mirror/subscription URLs (e.g., restrict to `http`/`https`, reject loopback/link-local/private/multicast address ranges by default, and make egress targets configurable/allow-listed).
- Consider requiring mirror coins to be created/spent by the store's actual owner (or otherwise cryptographically bound to store authority) before being trusted as sync sources, rather than accepting any coin sent to the public mirror puzzle.
- Rate-limit and bound retries/backoff more aggressively per destination host (not just per store id) to reduce DoS amplification potential, and add SSRF-style destination checks in `http_download()`/`download_file()` prior to issuing the request.

### Proof of Concept
1. Attacker (any wallet with minimal XCH) creates a mirror coin for a victim's (or arbitrary) `launcher_id` with `urls=["http://<attacker-or-victim-target>:<port>/"]` and `amount=0`, using `dl_new_mirror` / `create_new_mirror()` as shown in `chia/_tests/wallet/db_wallet/test_dl_wallet.py:795-812`.
2. Broadcast the resulting spend bundle; it confirms on chain like any standard coin spend.
3. Any DataLayer node that already tracks/subscribes to that `launcher_id` picks up the new mirror via `coin_added()` and stores it via `dl_store.add_mirror`.
4. On the next `periodically_manage_data()` cycle, `update_subscriptions_from_wallet()` merges the attacker URL into the subscription's server list, and `fetch_and_validate()`/`download_file()`/`http_download()` issues an HTTP GET to the attacker-controlled destination — repeating every `manage_data_interval` seconds with no destination validation.

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

**File:** chia/_tests/wallet/db_wallet/test_dl_wallet.py (L795-812)
```python
    await wallet_environments.process_pending_states(
        [
            WalletStateTransition(
                pre_block_balance_updates={
                    "xch": {},
                    "dl": {"pending_coin_removal_count": 1},
                },
                post_block_balance_updates={
                    "xch": {},
                    "dl": {"pending_coin_removal_count": -1},
                },
            )
        ]
    )
    await time_out_assert(15, is_singleton_confirmed_and_root, True, dl_wallet, launcher_id, bytes32([2] * 32))

    async with dl_wallet.wallet_state_manager.new_action_scope(DEFAULT_TX_CONFIG, push=True) as action_scope:
        await dl_wallet.create_new_mirror(launcher_id, uint64(0), [b"foo", b"bar"], action_scope)
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

**File:** chia/data_layer/data_layer.py (L965-985)
```python
    async def add_mirror(self, store_id: bytes32, urls: list[str], amount: uint64, fee: uint64) -> None:
        if not urls:
            raise RuntimeError("URL list can't be empty")
        await self.wallet_rpc.dl_new_mirror(
            DLNewMirror(launcher_id=store_id, amount=amount, urls=urls, fee=fee, push=True), DEFAULT_TX_CONFIG
        )

    async def delete_mirror(self, coin_id: bytes32, fee: uint64) -> None:
        await self.wallet_rpc.dl_delete_mirror(DLDeleteMirror(coin_id=coin_id, fee=fee, push=True), DEFAULT_TX_CONFIG)

    async def get_mirrors(self, store_id: bytes32) -> list[Mirror]:
        mirrors: list[Mirror] = (await self.wallet_rpc.dl_get_mirrors(DLGetMirrors(launcher_id=store_id))).mirrors
        return [mirror for mirror in mirrors if mirror.urls]

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

**File:** chia/data_layer/data_layer_rpc_api.py (L360-371)
```python
    async def subscribe(self, request: dict[str, Any]) -> EndpointResult:
        """
        subscribe to singleton
        """
        store_id = request.get("id")
        if store_id is None:
            raise Exception("missing store id in request")

        store_id_bytes = bytes32.from_hexstr(store_id)
        urls = request.get("urls", [])
        await self.service.subscribe(store_id=store_id_bytes, urls=urls)
        return {}
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
