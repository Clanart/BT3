### Title
Unauthenticated SSRF via forged DataLayer mirror coins causing arbitrary URL fetch - (File: chia/data_layer/data_layer.py, chia/data_layer/download_data.py)

### Summary
DataLayer mirror coins are ordinary coins created with a fixed puzzle hash (`create_mirror_puzzle()`) and a `CREATE_COIN` condition whose memos encode a `launcher_id` and an arbitrary list of URL strings. Any unprivileged spend-bundle submitter can create such a coin in their own transaction (no ownership of the target store, no special authorization, and no validation of the store id or URL content is performed at the puzzle/consensus layer). Any node that tracks/subscribes to that `launcher_id` will index this coin as a legitimate "mirror" for that store and will subsequently make outbound HTTP requests to the attacker-supplied URL when trying to sync/download data for that store — this mirrors the "unauthenticated URL fetch controlled by a low-privilege party" bug class in the MagicMirror `/cors` SSRF advisory.

### Finding Description
The mirror puzzle is a fixed, well-known puzzle hash: [1](#0-0) 

Any wallet — not just the DataLayer wallet that owns the singleton — can create a coin with this puzzle hash and arbitrary memo content by simply issuing a standard XCH spend with a `CREATE_COIN` condition to `MIRROR_PUZZLE_HASH` with `memos=[launcher_id, url1, url2, ...]`, exactly as `create_new_mirror()` does: [2](#0-1) 

The coin is recognized as a mirror purely by puzzle-hash + condition pattern matching, with no check that the spender is the actual DataLayer owner of `launcher_id`: [3](#0-2) 

Once confirmed on chain, any node syncing wallet state indexes it into the local `mirrors` table keyed only by `launcher_id`, with no distinction verifying the creator is authoritative for that store: [4](#0-3) 

The DataLayer service pulls these mirror URLs for a tracked store and treats them as legitimate remote server endpoints to fetch delta/full-tree files from: [5](#0-4) 

During normal sync (`fetch_and_validate`), the service iterates over `get_available_servers_for_store` (populated from these mirror URLs) and calls `insert_from_delta_file`, which in turn calls `download_file`/`http_download`, performing an unauthenticated outbound HTTP GET to the attacker-controlled URL with no destination validation (no blocklist for private/link-local/metadata IP ranges): [6](#0-5) [7](#0-6) 

### Impact Explanation
Any spend-bundle submitter who knows (or guesses) the `launcher_id` of a store that a victim DataLayer node tracks (owned stores and any subscribed store are candidates, and store ids are often publicly known/shared for offers) can force that node to issue outbound HTTP requests to arbitrary URLs, including cloud metadata endpoints (`169.254.169.254`), internal-network services, or attacker-controlled hosts, by publishing a low-cost coin with a forged mirror memo. This is a server-side request forgery reachable purely through normal spend-bundle submission (no peer/network-layer compromise, no operator action, no private key leak needed) — matching the CWE-918 pattern in the source advisory. Because the attacker fully controls the destination URL and the response content flows into file-write and parsing logic (`insert_from_delta_file` → `insert_into_data_store_from_file`), this could additionally be leveraged to probe internal services or waste node resources by pointing to large/slow endpoints, though it does not by itself expose secrets like the MagicMirror bug (there is no analogous environment-variable placeholder expansion here).

### Likelihood Explanation
Exploitability requires only a standard, syntactically valid spend bundle creating a coin at the known `MIRROR_PUZZLE_HASH` with attacker-chosen memos — something any wallet holding a small amount of XCH can construct and submit to the mempool. No special privilege, ownership of the singleton, or RPC/API access is required. The main precondition is that a target DataLayer node is actively tracking/subscribed to the targeted `launcher_id`, which is common for public/shared DataLayer stores (e.g., those referenced in offers or public data feeds).

### Recommendation
- Do not treat unauthenticated on-chain mirror coin memos as unconditionally trustworthy download endpoints; validate/restrict mirror URLs to a scheme and destination allowlist (reject private/link-local/loopback/metadata IP ranges) before use in `http_download`/`download_file`.
- Consider requiring a stronger binding between mirror-coin creator identity and the store owner (e.g., signature or singleton lineage proof) rather than mempool-observable puzzle-hash/memo pattern matching alone.
- Add SSRF-hardening at the HTTP client layer used in `chia/data_layer/download_data.py` (DNS resolution checks against RFC1918/link-local ranges, redirect restriction, response size/time limits already partially present via `max_delta_file_size`).

### Proof of Concept
1. Attacker crafts a standard wallet spend bundle with a `CREATE_COIN` condition: puzzle hash = `create_mirror_puzzle().get_tree_hash()`, amount = minimal (even `0`), memos = `[victim_launcher_id, b"http://169.254.169.254/latest/meta-data/"]` (or an attacker-controlled collector URL), mirroring the shape produced by `create_new_mirror()`: [2](#0-1) 
2. Submit this spend bundle to the mempool; it requires no interaction with the DataLayer RPC (`add_mirror`) and no ownership of `victim_launcher_id`.
3. Once confirmed, any node (including the victim's) syncing wallet state for `victim_launcher_id` records this as a legitimate mirror via `DataLayerStore.add_mirror`: [4](#0-3) 
4. On the victim's next sync cycle, `fetch_and_validate` retrieves this URL from `get_available_servers_for_store` and issues an outbound HTTP GET via `http_download`: [6](#0-5) [7](#0-6) 

Note: Due to indexing limits, the exact code path that syncs mirror coins from confirmed blocks into `DataLayerStore.add_mirror` (the wallet-side coin-state callback in `data_layer_wallet.py`) could not be fully retrieved in this session; a Devin session with full repository access would be needed to confirm whether any additional authorization check exists there before a mirror record is written.

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

**File:** chia/data_layer/dl_wallet_store.py (L308-333)
```python
    async def add_mirror(self, mirror: Mirror) -> None:
        """
        Add a mirror coin to the DB
        """

        async with self.db_wrapper.writer_maybe_transaction() as conn:
            await conn.execute_insert(
                "INSERT OR REPLACE INTO mirrors VALUES (?, ?, ?, ?, ?)",
                (
                    mirror.coin_id,
                    mirror.launcher_id,
                    mirror.amount.stream_to_bytes(),
                    b"".join(
                        [uint16(len(url)).stream_to_bytes() + url for url in Mirror.encode_urls(mirror.urls)]
                    ),  # prefix each item with a length
                    1 if mirror.ours else 0,
                ),
            )
            await conn.execute_insert(
                "INSERT OR REPLACE INTO mirror_confirmations (coin_id, confirmed_at_height) VALUES (?, ?)",
                (
                    mirror.coin_id,
                    mirror.confirmed_at_height,
                ),
            )

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
