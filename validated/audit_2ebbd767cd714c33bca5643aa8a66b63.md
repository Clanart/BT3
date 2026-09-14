### Title
DataLayer Mirror URL Fetch is Vulnerable to SSRF via Attacker-Injected Mirror Coins and Unvalidated Redirects - ([File: chia/data_layer/download_data.py])

### Summary
The DataLayer service resolves mirror URLs for a store from on-chain "mirror coins" and then fetches delta/full-tree files from those URLs over plain HTTP using `aiohttp` with default redirect-following behavior and no destination validation (no scheme/host/IP allow-listing, no protection against private/loopback/link-local/metadata addresses, and no re-validation of `Location` redirect targets). Critically, the mirror coin puzzle used to register these URLs (`create_mirror_puzzle()`) is a bare `P2_PARENT` puzzle whose only enforced condition is a `CREATE_COIN` to a fixed puzzle hash; the `launcher_id`/URL data is carried purely in unauthenticated CREATE_COIN memos, so **any unprivileged spend-bundle submitter** can register malicious mirror URLs for **any store id**, including stores they do not own.

### Finding Description
Mirror registration is implemented as a standard coin spend that creates a coin to `create_mirror_puzzle().get_tree_hash()` and stuffs `[launcher_id, *urls]` into the `CREATE_COIN` memos: [1](#0-0) 

`create_mirror_puzzle()` is just `P2_PARENT.curry(Program.to(1))` — an anyone-can-spend-style puzzle with no restriction tying the memo contents to a signer or to store ownership: [2](#0-1) 

When any coin with this puzzle hash is seen on-chain, every wallet tracking the referenced `launcher_id` (i.e. any node subscribed to that DataLayer store, not just the owner) accepts the embedded URLs as a legitimate mirror, with no checks on the URL content: [3](#0-2) 

These wallet-tracked mirrors then feed directly into the DataLayer service's periodic sync loop. `update_subscriptions_from_wallet()` pulls the (attacker-controlled) URLs straight from wallet RPC with no filtering: [4](#0-3) 

`fetch_and_validate()` then randomly selects one of these server URLs and downloads delta files from it: [5](#0-4) 

The actual network request is made in `http_download()`, using `aiohttp.ClientSession.get()` with no `allow_redirects=False`, no scheme restriction, and no destination-IP validation of the initial URL or of any redirect target: [6](#0-5) 

There is no equivalent of an `IsSSRFSafeURL()` check anywhere in `chia/data_layer/` — a search for private-IP/localhost/SSRF validation logic in this module returns nothing. Unlike the WeKnora report where the initial URL at least passes validation before a redirect bypasses it, here **the initial URL itself is completely unvalidated** because it originates from unauthenticated on-chain memo data controlled by an arbitrary spend-bundle submitter, and `aiohttp`'s default behavior of following up to 10 redirects means an attacker-controlled first hop can redirect the request anywhere, including loopback, link-local metadata endpoints, or other services in the operator's internal network/Docker network.

### Impact Explanation
Any user who can submit a valid spend bundle (spending only their own coin, of arbitrary/dust amount) can force any node running the DataLayer service and subscribed to a given store to make outbound HTTP requests to attacker-chosen destinations, including internal-only services. Because the response is fed into `insert_from_delta_file()`/`DataStore.insert_into_data_store_from_file()`, this is not merely a blind SSRF — response content is parsed as (untrusted) delta-file content, and errors/log output can leak information about internal service responses. This can be used for internal network reconnaissance, hitting internal admin/metadata endpoints reachable from the DataLayer host, and disrupting/DoS-ing DataLayer sync by causing repeated outbound requests to arbitrary hosts. This matches CWE-918 SSRF impact class at Medium severity (confidentiality impact from internal network access), consistent with the reference advisory's CVSS.

### Likelihood Explanation
Likelihood is high for any node operating a DataLayer service that subscribes to third-party stores (the intended use case for a decentralized data layer): mirror coins are a normal, permissionless part of the protocol, and nothing prevents a third party from registering a mirror for a `launcher_id` they don't own. No special privileges, wallet keys, or CLI access are required — only the ability to broadcast a standard spend bundle creating a coin with a specific puzzle hash and memo, which is squarely within the capability of an unprivileged spend-bundle submitter.

### Recommendation
- Restrict mirror registration authority so that only the DataLayer store owner's signature/authorization is honored for a given `launcher_id`, or otherwise validate that mirror coins for a launcher are created by an authorized party before storing them as usable subscription URLs.
- Add SSRF-safe URL validation (scheme allow-list, DNS resolution + private/loopback/link-local/metadata-IP blocking, and Docker-specific hosts) before any HTTP fetch in `chia/data_layer/download_data.py`.
- Disable automatic redirect following (`allow_redirects=False`) in `http_download()` and re-validate any redirect target against the same SSRF-safe rules before manually following it, or reject redirects entirely.

### Proof of Concept
1. Attacker submits a standard spend bundle spending a small owned coin, creating a `CREATE_COIN` to `create_mirror_puzzle().get_tree_hash()` with `memos = [victim_launcher_id, b"http://169.254.169.254/latest/meta-data/"]` (or `http://<internal-host>:<port>/`), as done by `DataLayerWallet.create_new_mirror()`.
2. Once confirmed on chain, any node with `DataLayerWallet` tracking `victim_launcher_id` (e.g., any subscriber to that public DataLayer store) records this as a legitimate `Mirror` via `coin_added()`.
3. On its next `periodically_manage_data()` cycle, that node's `update_subscriptions_from_wallet()` and `fetch_and_validate()` pick the attacker's URL and `http_download()` issues an unauthenticated, unvalidated HTTP GET to the attacker-chosen internal/metadata address, following any redirects automatically.

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
