### Title
DataLayer Mirror URLs Allow Any User to Trigger SSRF Against Subscriber Nodes via Unrestricted `http_download` Fetch - (File: `chia/data_layer/download_data.py`)

### Summary
Chia's DataLayer subsystem lets any wallet holder publish "mirror" URLs on-chain for *any* DataLayer store (not just stores they own), via `DataLayerWallet.create_new_mirror()`. These URLs are later read back by every node that subscribes to that store and are fed directly into `aiohttp` HTTP requests with no scheme/host validation, allowing an unprivileged chain participant to make a victim's DataLayer service issue outbound HTTP requests to attacker-chosen hosts (e.g., `127.0.0.1`, cloud metadata IPs, or internal-network services) — the same bug class as the Admidio `fetch_metadata.php` SSRF (`FILTER_VALIDATE_URL` accepting attacker URLs later passed unchecked to `file_get_contents`).

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a `CREATE_COIN` with memos `[launcher_id, *urls]` and has no check that the caller owns or controls `launcher_id`: [1](#0-0) 

Any user can therefore submit a spend bundle (via `dl_new_mirror`/`add_mirror`) that creates a mirror coin memo-tagging a store they don't own with arbitrary URL strings: [2](#0-1) [3](#0-2) 

These on-chain memos are parsed back into `(launcher_id, urls)` pairs with no validation of scheme or host: [4](#0-3)  and persisted verbatim into the wallet's mirror table: [5](#0-4) 

A DataLayer service subscribed to that store id then pulls these mirror URLs via `dl_get_mirrors` and stores them as subscription server info with no filtering: [6](#0-5) 

During the periodic sync loop, `fetch_and_validate()` selects one of these attacker-supplied URLs and passes it to `insert_from_delta_file()` → `download_file()` → `http_download()`, which performs an `aiohttp` GET directly against `server_info.url` with no allowlist, no private/reserved IP blocking, and no scheme restriction: [7](#0-6) [8](#0-7) 

This mirrors the Admidio flaw exactly: user-controlled URL strings are validated only for syntactic well-formedness (`is_filename_valid` only checks the requested *filename*, not the *URL host*) before being handed to an HTTP client that will happily connect to loopback, link-local, or internal RFC1918 addresses.

### Impact Explanation
An attacker (any wallet holder able to submit a transaction, no special privilege) can force any Data Layer node that subscribes to a target store to issue outbound HTTP requests to attacker-chosen destinations:
- SSRF to internal services (Redis, internal admin panels, database endpoints) reachable from the victim's DataLayer host.
- SSRF to cloud instance metadata endpoints (`http://169.254.169.254/...`) on cloud-hosted DataLayer nodes, potentially exposing IAM credentials.
- Because `http_download` writes the (attacker-influenced) response bytes to disk and feeds them into `insert_into_data_store_from_file`, malformed or unexpected content from an internal target could also be used to probe internal service behavior or cause repeated retry/log noise (though genuine store corruption is bounded by root-hash verification during insertion).

This satisfies the "coin-set divergence"/reachable-SSRF class described in the analog rules: the trigger is a standard, unprivileged spend bundle (mirror-coin creation) reachable by any wallet user or Data Layer client, and it causes unauthorized network egress from the victim's own infrastructure — a direct network-reachability/SSRF impact analogous to the Admidio CVE.

### Likelihood Explanation
Likelihood is high for any node that actively subscribes to third-party stores (the intended DataLayer use case: subscribing to public stores for replication). Creating a malicious mirror costs only the standard mirror-coin fee/amount and is fully reachable by an ordinary spend bundle — no admin/operator access, no malicious-peer or protocol-level exploit is required, matching the "unprivileged submitter" reachability requirement.

### Recommendation
- Validate mirror/subscription URLs before storing/using them: enforce `https://` (or an explicit allowlist of schemes), and resolve+reject hosts that map to loopback, link-local, or private/reserved IP ranges (mirroring PHP's `FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE` recommendation from the source report) both at mirror-creation time (`create_new_mirror`) and at fetch time (`http_download`, `download_file`).
- Consider re-validating destination IPs at connection-time (not just DNS-resolution time) to mitigate DNS-rebinding, since `aiohttp` resolves lazily.
- Add an operator-configurable trust boundary (e.g., only fetch from mirrors for stores the node has explicitly opted to subscribe to, and warn/require confirmation when a new external mirror URL first appears).

### Proof of Concept
1. Attacker (unprivileged wallet holder) calls the wallet RPC `dl_new_mirror` for a `launcher_id` corresponding to any DataLayer store id (owned or not), with `urls=["http://127.0.0.1:6379/"]` (or an internal admin URL), and pushes the resulting spend bundle to the mempool — this is a completely standard, permissionless transaction: [2](#0-1) 
2. Once confirmed, any node subscribed to that store id calls `update_subscriptions_from_wallet()`, which pulls this URL from chain state and stores it as a subscription server: [6](#0-5) 
3. On the node's next periodic sync cycle, `fetch_and_validate()`/`insert_from_delta_file()` calls `http_download()`, issuing `GET http://127.0.0.1:6379/<delta_filename>` from the victim node, reaching internal services: [8](#0-7) 

**Confidence caveat:** I was unable to fully confirm within the tool budget whether any additional URL/host filtering exists deeper in `data_store.py`'s `subscribe()`/`update_subscriptions_from_wallet()` implementation (the file content for those specific functions was not retrieved before the iteration limit); based on all code paths inspected (`data_layer.py`, `download_data.py`, `data_layer_wallet.py`, `db_wallet_puzzles.py`, `dl_wallet_store.py`) no scheme or private-IP validation is present anywhere in the mirror/subscription/download pipeline. A Devin session with full file access should verify `DataStore.subscribe()` and `DataStore.update_subscriptions_from_wallet()` in `chia/data_layer/data_store.py` to rule out any existing filtering before treating this as fully confirmed.

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

**File:** chia/data_layer/data_layer.py (L965-970)
```python
    async def add_mirror(self, store_id: bytes32, urls: list[str], amount: uint64, fee: uint64) -> None:
        if not urls:
            raise RuntimeError("URL list can't be empty")
        await self.wallet_rpc.dl_new_mirror(
            DLNewMirror(launcher_id=store_id, amount=amount, urls=urls, fee=fee, push=True), DEFAULT_TX_CONFIG
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

**File:** chia/data_layer/dl_wallet_store.py (L308-332)
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
