### Title
DataLayer mirror-URL SSRF: attacker-controlled coin memos drive unauthenticated HTTP fetches by any DataLayer node - (File: chia/data_layer/data_layer_wallet.py, chia/data_layer/download_data.py)

### Summary
Any user can submit a spend bundle that creates a "mirror" coin using the anyone-can-spend `MIRROR_PUZZLE_HASH` puzzle, embedding an arbitrary `launcher_id` and arbitrary URL strings in the coin's memos. Any node running the DataLayer service that is tracking that `launcher_id` (i.e., subscribed to that store) will parse those memos via `get_mirror_info()`, persist the attacker-chosen URLs as `ServerInfo.url` candidates for that store, and later have its background sync loop fetch content from that URL via `http_download()`/`download_file()` with no validation of hostname or IP address, mirroring the LMDeploy SSRF root cause (no blocklist for internal IPs, metadata endpoints, etc.).

### Finding Description
`create_mirror_puzzle()` curries `P2_PARENT` with `Program.to(1)`, an anyone-can-spend condition puzzle requiring no signature to create a mirror coin: [1](#0-0) 

When such a coin is created, `coin_added()` in `DataLayerWallet` detects the `MIRROR_PUZZLE_HASH`, fetches the parent spend, and calls `get_mirror_info()` to extract the `launcher_id` and URL list straight from the coin's memos, with no sanitization of the URL strings, only checking whether `is_launcher_tracked(launcher_id)`: [2](#0-1) 

`get_mirror_info()` itself just decodes memo bytes to a `launcher_id` and raw URL byte strings with no format/scheme/IP validation: [3](#0-2) 

Once stored, these mirror URLs become `ServerInfo` candidates that the DataLayer sync loop treats as legitimate download servers. `fetch_and_validate()` randomly selects a server URL for a subscribed store and calls `insert_from_delta_file()`, which calls `download_file()`, which — when no plugin downloader is configured — calls `http_download()`: [4](#0-3) [5](#0-4) 

`http_download()` performs a raw `aiohttp` GET request to `server_info.url + "/" + filename` with zero validation that the URL does not resolve to loopback, link-local (e.g. `169.254.169.254` cloud metadata), or RFC1918 private ranges — exactly the missing check described in the LMDeploy advisory: [6](#0-5) 

Notably, `is_filename_valid()` and store-id checks constrain the *filename* requested, but never constrain the *destination host* of the request, so the request goes to whatever host the attacker encoded in the mirror coin's memos.

### Impact Explanation
Any wallet owner who tracks (subscribes to) a DataLayer store can be forced by an unrelated third party — anyone able to broadcast a spend bundle creating a mirror coin for that `launcher_id` — into having their DataLayer service issue arbitrary outbound HTTP requests. Because DataLayer nodes commonly run in cloud/self-hosted environments, this can be used to probe/reach internal services, hit cloud metadata endpoints to steal instance credentials, or perform internal network reconnaissance — the same class of impact described in the LMDeploy report (CWE-918, cloud credential theft, internal service access). The response body is not directly returned to the attacker in current DataLayer flow (it's saved to disk and validated as a delta/full file, so blind data exfiltration is not trivial), which limits the impact relative to LMDeploy's direct response-echo case, but the SSRF request itself (GET to arbitrary attacker-chosen host/port with attacker-chosen path segment via `filename`, though filename must satisfy `is_filename_valid`) is still triggered without any authorization from the subscribing node's operator.

### Likelihood Explanation
Creating a mirror coin is permissionless (anyone-can-spend puzzle, no signature required) and only requires broadcasting a standard spend bundle with a `CREATE_COIN` condition to `MIRROR_PUZZLE_HASH` with the target `launcher_id` and URL memos, which is well within reach of an unprivileged spend-bundle submitter. Exploitation requires that a victim node have already subscribed to (be tracking) the targeted `launcher_id`/store, which is a common DataLayer usage pattern (subscribing to third-party stores for replication) and is not itself a privileged action from the attacker's perspective — the attacker only needs the target's `launcher_id`, which is public on-chain.

### Recommendation
Validate all mirror/server URLs before storing or using them for HTTP fetches in `get_mirror_info()` / `coin_added()` and again in `http_download()`: enforce an allowed scheme (http/https), resolve the hostname, and reject loopback, link-local, and private/reserved IP ranges (mirroring the `is_safe_url()` pattern from the LMDeploy fix) before persisting a `Mirror`/`ServerInfo` and before every outbound request in `download_file()`/`http_download()`. Consider also capping the number of distinct mirror URLs accepted per launcher and treating newly-seen mirror URLs as untrusted until validated.

### Proof of Concept
1. Attacker identifies a public `launcher_id` for a DataLayer store that a target node is known to subscribe to (subscriptions/launcher IDs are visible on-chain and via `dl_history`).
2. Attacker constructs and broadcasts a spend bundle that spends any coin, creating a new coin with puzzle hash `MIRROR_PUZZLE_HASH` (`create_mirror_puzzle()` in `chia/wallet/db_wallet/db_wallet_puzzles.py`), setting memos `[launcher_id, b"http://169.254.169.254"]` (or an internal RFC1918 IP under attacker control).
3. Once the spend bundle is included in a block, any node that is tracking that `launcher_id` (`is_launcher_tracked`) picks up the coin in `coin_added()` (`chia/data_layer/data_layer_wallet.py:775-799`), decodes the URL via `get_mirror_info()`, and stores it as a `Mirror`/subscription server URL.
4. On the node's next `periodically_manage_data()` / `fetch_and_validate()` cycle, `get_available_servers_for_store()` may select this malicious URL, and `insert_from_delta_file()` → `download_file()` → `http_download()` issues an outbound `aiohttp` GET to `http://169.254.169.254/<delta-filename>` from the victim's server, with no IP/host validation performed anywhere in this path.

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
