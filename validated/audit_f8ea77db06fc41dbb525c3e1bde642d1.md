### Title
Server-Side Request Forgery via unauthenticated DataLayer "mirror" coins - (File: chia/data_layer/data_layer.py)

### Summary
Any wallet holder who can spend a small XCH amount can create an on-chain "mirror" coin that advertises an attacker-chosen URL for a DataLayer store the victim already tracks/subscribes to. The victim's `DataLayer` service ingests this mirror announcement without checking who created it or where it points, and later issues outbound HTTP requests to that attacker-controlled URL. This mirrors the BookWyrm SSRF pattern (an under-validated, remotely-influenced URL leads the server to make attacker-directed HTTP requests), except here the "unprivileged submitter" is anyone able to spend a coin through the trivially-spendable mirror puzzle.

### Finding Description
DataLayer mirror coins use a puzzle with no ownership restriction: `create_mirror_puzzle()` is just `P2_PARENT.curry(Program.to(1))`, i.e. it can be spent by anyone who owns the parent coin, and its memo encodes an arbitrary `launcher_id` plus arbitrary URL strings. [1](#0-0) 

When a node's `DataLayerWallet` sees a coin spent to this puzzle, it decodes the `launcher_id`/URLs from the parent solution and stores the mirror as long as the wallet is merely *tracking* (subscribed to) that `launcher_id` — there is no check that the spender owns the launcher/store, only `is_launcher_tracked`: [2](#0-1) 

The wallet-side store persists and later returns *all* mirrors for a launcher id, with no filtering by the `ours` flag: [3](#0-2) 

The `DataLayer` service's `get_mirrors()`/`update_subscriptions_from_wallet()` pulls these URLs straight from wallet RPC (`dl_get_mirrors`) with only an "urls non-empty" filter — again, no ownership/allowlist check — and feeds them into the local subscription/server table: [4](#0-3) 

The periodic sync loop `fetch_and_validate()` later randomizes and iterates over these attacker-supplied server URLs and calls `insert_from_delta_file()`/`download_file()`, which — absent a plugin downloader — calls `http_download()`: [5](#0-4) 

`http_download()` performs an unrestricted `aiohttp` GET against `server_info.url + "/" + filename` with no scheme/host allow-listing (e.g., no block on `http://127.0.0.1`, link-local/metadata addresses, or internal hostnames): [6](#0-5) 

The project's own test suite demonstrates that arbitrary, even loopback, URLs are accepted and stored as mirrors without restriction: `urls = ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]`. [7](#0-6) 

### Impact Explanation
Any user able to spend a coin (a very low cost, unprivileged action — no ownership of the targeted DataLayer store or singleton is required) can force any node that tracks/subscribes to that store to issue outbound HTTP requests to a URL of the attacker's choosing. This is server-side request forgery: an attacker can direct victim `DataLayer` nodes to probe/attack internal network services, cloud metadata endpoints, or other otherwise-unreachable hosts, using the victim node as a request proxy. Because the mirror coin can name any tracked `launcher_id`, the attack is not limited to stores the attacker owns — it works against any store a victim subscribes to.

### Likelihood Explanation
Likelihood is high for any operator running a DataLayer node with subscriptions to third-party stores (the common case, since DataLayer is designed around store consumers subscribing to producer-published stores). The only requirement for the attacker is the ability to spend a coin to the well-known `create_mirror_puzzle()` puzzle hash with a crafted memo — no special key material, no counterparty cooperation, and no knowledge beyond a target `launcher_id`, which is public.

### Recommendation
- Restrict which mirror announcements are trusted/acted upon: require that mirror coins be created by (or otherwise attributable to) the store owner, or explicitly opt-in per-mirror-coin before using its URL for outbound requests, rather than accepting any mirror for any tracked launcher_id.
- Validate/restrict destination URLs before issuing outbound requests from `http_download()`/`download_file()` (block loopback, link-local, private, and metadata-service address ranges; restrict schemes to `http`/`https`; optionally support an operator-configurable allowlist).
- Consider rate-limiting/backoff already present (`server_misses_file`) combined with an explicit user confirmation step before a newly-seen third-party mirror URL is contacted for the first time.

### Proof of Concept
1. Attacker identifies a `launcher_id` (DataLayer store id) that the victim node subscribes to (store ids are public).
2. Attacker spends any coin to `create_mirror_puzzle()`'s puzzle hash with memo `[launcher_id, b"http://169.254.169.254/latest/meta-data/"]` (or any internal victim-reachable URL), per the `create_new_mirror` mechanism: [8](#0-7) 
3. Once confirmed on-chain, the victim's wallet ingests this as a `Mirror` record for the tracked `launcher_id` via `coin_added()` (no ownership check).
4. The victim's `DataLayer.update_subscriptions_from_wallet()` pulls this URL into its subscription/server table, and the next `fetch_and_validate()` cycle causes `http_download()` to issue a GET request to the attacker-chosen URL.

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

**File:** chia/data_layer/dl_wallet_store.py (L334-352)
```python
    async def get_mirrors(self, launcher_id: bytes32) -> list[Mirror]:
        async with self.db_wrapper.reader_no_transaction() as conn:
            cursor = await conn.execute(
                "SELECT * from mirrors WHERE launcher_id=?",
                (launcher_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            mirrors: list[Mirror] = []

            for row in rows:
                confirmation_height = await execute_fetchone(
                    conn, "SELECT * FROM mirror_confirmations WHERE coin_id=?", (row[0],)
                )
                mirrors.append(
                    _row_to_mirror(row, None if confirmation_height is None else uint32(confirmation_height[1]))
                )

        return mirrors
```

**File:** chia/data_layer/data_layer.py (L642-705)
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
                if success:
                    self.log.info(
                        f"Finished downloading and validating {store_id}. "
                        f"Wallet generation saved: {singleton_record.generation}. "
                        f"Root hash saved: {singleton_record.root}."
                    )
                    break
            except aiohttp.client_exceptions.ClientConnectorError:
                self.log.warning(f"Server {url} unavailable for {store_id}.")
            except Exception as e:
                self.log.warning(f"Exception while downloading files for {store_id}: {e} {traceback.format_exc()}.")
```

**File:** chia/data_layer/data_layer.py (L975-985)
```python
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

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2782-2791)
```python
        urls = ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        res = await data_rpc_api.add_mirror({"id": store_id.hex(), "urls": urls, "amount": 1, "fee": 1})

        await farm_block_check_singleton(data_layer, full_node_api, ph, store_id, wallet=wallet_rpc_api.service)
        mirrors = await data_rpc_api.get_mirrors({"id": store_id.hex()})
        mirror_list = mirrors["mirrors"]
        assert len(mirror_list) == 1
        mirror = mirror_list[0]
        assert mirror["urls"] == ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        coin_id = mirror["coin_id"]
```
