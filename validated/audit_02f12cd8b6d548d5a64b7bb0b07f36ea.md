I have enough to write up the finding.

### Title
Server-Side Request Forgery (SSRF) via unauthenticated DataLayer mirror URLs - (File: chia/data_layer/download_data.py)

### Summary
Any wallet holder can publish an on-chain "mirror" coin for a DataLayer store that embeds arbitrary attacker-chosen URLs. Any Chia node that owns, tracks, or is subscribed to that store id will automatically pull those URLs into its local subscription list and periodically issue outbound HTTP GET requests to them with no scheme/host/IP validation. This lets an unprivileged, low-cost spend-bundle submitter force victim `DataLayer` services (and any node that tracks the same store) to perform requests against attacker-controlled or internal network targets — the same bug class described in the referenced Apache CloudStack webhook SSRF advisory (CVE-2026-59085), where user-supplied delivery/callback URLs were dereferenced by the server without validation.

### Finding Description
DataLayer mirrors are ordinary coins spent to `create_mirror_puzzle()`, whose memos encode a `launcher_id` and a list of raw URL strings [1](#0-0) . Creating one only requires calling `dl_new_mirror` / `DataLayerWallet.create_new_mirror`, which builds a standard signed transaction with the URLs placed unmodified into the coin's memos — there is no validation of URL scheme, host, or target address [2](#0-1) [3](#0-2) . The mirror amount can even be `0`, so this requires only the transaction fee [4](#0-3) .

Any node that tracks the corresponding `launcher_id` (via `dl_track_new`, by owning the store, or simply because it previously subscribed) picks up this coin in `DataLayerWallet.coin_added`, decodes the URLs from the memos with no filtering, and records them as a `Mirror` [5](#0-4) . The `DataLayer` service coordinator periodically calls `update_subscriptions_from_wallet`, which reads all mirror URLs for the store and merges them into the local `subscriptions` table verbatim [6](#0-5) [7](#0-6) .

During the sync loop, `fetch_and_validate()` pulls the (now attacker-controlled) `servers_info` list and, for each entry, calls `insert_from_delta_file()` → `download_file()` [8](#0-7) [9](#0-8) . When no downloader plugin is configured (the default), `download_file()` calls `http_download()`, which performs a raw `aiohttp` GET to `server_info.url + "/" + filename` with no restriction on scheme, hostname, or IP range (no blocking of loopback, link-local/metadata addresses, or private RFC1918 ranges) [10](#0-9) . The only "validation" anywhere in this path (`is_filename_valid`) checks the *filename* format, never the *URL* [11](#0-10) .

### Impact Explanation
This is a genuine SSRF: an unprivileged attacker who can submit any valid spend bundle (a mirror-coin spend costing only a fee, potentially amount `0`) causes other users' `DataLayer` processes to autonomously issue outbound HTTP requests to attacker-chosen targets — e.g. cloud metadata endpoints (`169.254.169.254`), internal-only services, or other local ports on the victim host (including its own unauthenticated internal RPC endpoints), whenever those nodes track/subscribe to the poisoned store id. Because the request is a plain GET with predictable path (`<url>/<filename>`), and the response is fed into local file-write and Merkle-tree ingestion logic (`insert_into_data_store_from_file`), the primitive can also be used to probe internal network reachability/banner information via response codes/timing, and to redirect a victim's outbound traffic at scale by getting many nodes to track a popular/interesting store id.

### Likelihood Explanation
Reaching this requires only: (1) publishing a mirror coin with attacker-chosen URLs (any unprivileged wallet user can do this for a store they know the launcher id of, at near-zero cost) and (2) getting a target node to track that launcher id, which happens automatically for any node that subscribes to or discovers the store through normal DataLayer usage, or already owns/tracks it. No signature/authorization over the URL content is required beyond a normal spend, and the periodic sync loop (`periodically_manage_data` → `update_subscription`) runs unattended, making exploitation fully automatic once a victim tracks the store.

### Recommendation
Validate mirror/subscription URLs before they are persisted or dereferenced: enforce an allow-list of schemes (e.g., `http`/`https` only), resolve and reject requests to loopback, link-local, multicast, and private/internal address ranges (unless explicitly opted in via config, e.g. for test/CI use), and consider requiring explicit user confirmation before a new mirror URL is added to the active subscription/fetch set rather than trusting on-chain-derived URLs automatically. Apply the same validation both to `update_subscriptions_from_wallet`/`DataStore.subscribe` ingestion and to `http_download`/`download_file` right before the outbound request is issued, so validation cannot be bypassed by a future alternate code path.

### Proof of Concept
1. Attacker creates (or knows) a DataLayer `launcher_id` and calls `dl_new_mirror` with `amount=0` and `urls=["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:<victim-internal-port>/"]` [3](#0-2) , then gets the transaction farmed on-chain (mirror amount can be 0 per existing test coverage) [4](#0-3) .
2. Victim's `DataLayer` node tracks/owns/subscribes to the same `launcher_id` (a common scenario for any store used in offers or shared data). `DataLayerWallet.coin_added` records the attacker's URLs as a `Mirror` [5](#0-4) .
3. `DataLayer.update_subscriptions_from_wallet` copies these URLs into the local `subscriptions` table [6](#0-5) .
4. On the next sync cycle, `fetch_and_validate` → `insert_from_delta_file` → `download_file` → `http_download` issues an outbound `GET http://169.254.169.254/latest/meta-data/...` (or the internal port) from the victim node, entirely server-initiated [10](#0-9) .

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L90-109)
```python
def create_mirror_puzzle() -> Program:
    return P2_PARENT.curry(Program.to(1))


MIRROR_PUZZLE_HASH = create_mirror_puzzle().get_tree_hash()


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

**File:** chia/wallet/wallet_rpc_api.py (L3255-3277)
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

        # tx_endpoint will take care of default values here
        return DLNewMirrorResponse(unsigned_transactions=[], transactions=[])
```

**File:** chia/_tests/wallet/db_wallet/test_dl_wallet.py (L811-812)
```python
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

**File:** chia/data_layer/download_data.py (L171-220)
```python
async def insert_from_delta_file(
    data_store: DataStore,
    store_id: bytes32,
    existing_generation: int,
    target_generation: int,
    root_hashes: list[bytes32],
    server_info: ServerInfo,
    client_foldername: Path,
    timeout: aiohttp.ClientTimeout,
    log: logging.Logger,
    proxy_url: str | None,
    downloader: PluginRemote | None,
    group_files_by_store: bool = False,
    maximum_full_file_count: int = 1,
    max_delta_file_size: int = 250,
) -> bool:
    if group_files_by_store:
        client_foldername.joinpath(f"{store_id}").mkdir(parents=True, exist_ok=True)

    delta_reader: DeltaReader | None = None

    for root_hash in root_hashes:
        timestamp = int(time.time())
        existing_generation += 1
        target_filename_path = get_delta_filename_path(
            client_foldername, store_id, root_hash, existing_generation, group_files_by_store
        )
        filename_exists = target_filename_path.exists()
        for grouped_by_store in (False, True):
            success = await download_file(
                data_store=data_store,
                target_filename_path=target_filename_path,
                store_id=store_id,
                root_hash=root_hash,
                generation=existing_generation,
                server_info=server_info,
                proxy_url=proxy_url,
                downloader=downloader,
                timeout=timeout,
                client_foldername=client_foldername,
                timestamp=timestamp,
                log=log,
                grouped_by_store=grouped_by_store,
                group_downloaded_files_by_store=group_files_by_store,
                max_delta_file_size=max_delta_file_size,
            )
            if success:
                break
        else:
            return False
```

**File:** chia/data_layer/download_data.py (L298-320)
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
```

**File:** chia/data_layer/data_layer_server.py (L91-103)
```python
    async def file_handler(self, request: web.Request) -> web.Response:
        filename = request.match_info["filename"]
        if not is_filename_valid(filename):
            raise Exception("Invalid file format requested.")
        file_path = self.server_dir.joinpath(filename)
        with open(file_path, "rb") as reader:
            content = reader.read()
        response = web.Response(
            content_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment;filename={filename}"},
            body=content,
        )
        return response
```
