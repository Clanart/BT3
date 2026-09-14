## Title
Blind Server-Side Request Forgery (SSRF) via attacker-controlled DataLayer mirror URLs published on-chain - (File: `chia/data_layer/download_data.py`)

### Summary
Any wallet user can create a DataLayer "mirror" coin (`dl_new_mirror`) whose spend memos contain arbitrary attacker-chosen strings that are later treated as HTTP server URLs. Any node that owns or subscribes to that DataLayer store will read the on-chain mirror URLs and issue outbound HTTP GET requests to them from its own DataLayer service, with no validation of scheme/host/port. This is a blind SSRF: an unprivileged spend (mirror coin creation) forces other users' services to make attacker-directed network requests, structurally analogous to CVE-2024-27098 where attacker-supplied data drives an SSRF-triggering object/target.

### Finding Description
`DataLayerWallet.create_new_mirror()` creates a coin with `memos=[[launcher_id, *(url for url in urls)]]` where `urls` is fully caller-supplied and unrestricted. [1](#0-0) 

The wallet RPC endpoint `dl_new_mirror` accepts this list of `urls: list[str]` from any wallet client without validation and pushes it directly to `create_new_mirror`. [2](#0-1) [3](#0-2) 

These mirror URLs are recovered on-chain by any peer parsing the mirror coin's spend via `get_mirror_info()`, which extracts `launcher_id` and raw `urls` from the coin's memos with no sanitization. [4](#0-3) 

`DataLayer.update_subscriptions_from_wallet()` pulls mirror URLs from wallet RPC and stores them as subscription server URLs for the corresponding store. [5](#0-4) 

During the periodic sync loop, `fetch_and_validate()` picks a URL from `get_available_servers_for_store()` and passes it into `insert_from_delta_file()` → `download_file()` → `http_download()`. [6](#0-5) 

`http_download()` performs an unrestricted `aiohttp` GET request to `server_info.url + "/" + filename` — the attacker-controlled URL — with only a byte-size cap on the response, no scheme/host allowlisting, no blocking of private/internal/link-local addresses (e.g., `169.254.169.254` cloud metadata, `localhost`, internal RPC ports), and it happens automatically without user interaction on any node that is subscribed/owns the store. [7](#0-6) 

The documented module context confirms mirror/plugin URLs are explicitly "external trust inputs" that are never sanitized as request targets, only downstream file contents are validated against the wallet-advertised Merkle root. [8](#0-7) 

### Impact Explanation
Any user who can submit a wallet-transaction (a normal, unprivileged action, not requiring special permissions) can force every other node running the DataLayer service that subscribes to the targeted store to issue outbound HTTP requests to a URL of the attacker's choosing. This can be used to:
- Probe/attack internal network services or cloud metadata endpoints reachable from the victim node (classic SSRF impact — internal port scanning, metadata credential theft on cloud-hosted nodes).
- Cause resource exhaustion/DoS against third parties by directing many DataLayer nodes to repeatedly request the same external target (reflection/amplification), since the sync loop retries this periodically per store subscription.
- Leak network topology/information about which hosts a subscribing node can reach, and time-based side channels.

This matches the CVE-2024-27098 bug class (server making authenticated-but-unprivileged-user-controlled requests to arbitrary targets), reachable purely from "Data Layer client" actions, consistent with in-scope categories.

### Likelihood Explanation
Likelihood is Medium: the action requires only a standard wallet transaction (`dl_new_mirror`) plus a low mojo amount/fee, and no admin/operator privilege. The victim behavior (auto-fetching mirror URLs) is on by default in the DataLayer sync loop for stores users have subscribed to or track. The main constraint is that a victim node must be actively subscribed to (or own) the targeted DataLayer store, and only GET requests to attacker-chosen HTTP(S) URLs are possible (not full arbitrary protocol smuggling), limiting exploitation to services reachable via plain HTTP GET.

### Recommendation
- Validate and restrict mirror/subscription URLs before they are used as outbound request targets: enforce an allow-list of schemes (http/https only), reject loopback/link-local/private/reserved IP ranges (unless explicitly configured for testing), and consider DNS resolution checks at request time to avoid DNS rebinding.
- Apply the same validation both when storing wallet-derived mirror URLs (`update_subscriptions_from_wallet`) and immediately before `http_download()`/`download_file()` issue the request.
- Consider requiring explicit user opt-in/allow-listing of mirror hosts, or rate-limiting/backoff for previously-unseen mirror hosts, in addition to the existing failure-based banning.

### Proof of Concept
1. Attacker creates a DataLayer store or uses an existing one they control, and any victim node subscribes to (or tracks) that store id.
2. Attacker calls `dl_new_mirror` via wallet RPC with `urls=["http://169.254.169.254/latest/meta-data/"]` (or any internal target), e.g.:
   ```
   client.dl_new_mirror(DLNewMirror(launcher_id=store_id, amount=uint64(1), urls=["http://169.254.169.254/latest/meta-data/"], fee=uint64(0), push=True), tx_config)
   ```
   as exercised in [9](#0-8) .
3. Once confirmed on-chain, the victim's `DataLayer.update_subscriptions_from_wallet()` and periodic sync (`fetch_and_validate()`) pick up this URL as a server to try, and `http_download()` performs `session.get(url + "/" + filename, ...)` against the attacker-chosen host with no validation, as shown in [10](#0-9) .
4. The victim node's outbound request to the attacker-selected target is fully blind (its content is not returned to the attacker directly through the protocol, but response codes/timing and the DataLayer failure/ban and retry cadence can be observed indirectly), demonstrating the SSRF condition.

### Citations

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

**File:** chia/wallet/wallet_request_types.py (L1853-1859)
```python
@streamable
@dataclass(frozen=True, kw_only=True)
class DLNewMirror(TransactionEndpointRequest):
    launcher_id: bytes32
    amount: uint64
    urls: list[str]

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

**File:** .cursor/context/data-layer.md (L27-27)
```markdown
- Do not treat mirror URLs, plugins, or static file names as trusted data sources.
```

**File:** chia/_tests/wallet/rpc/test_dl_wallet_rpc.py (L281-292)
```python
            txs = (
                await client.dl_new_mirror(
                    DLNewMirror(
                        launcher_id=launcher_id,
                        amount=uint64(1000),
                        urls=["foo", "bar"],
                        fee=uint64(2000000000000),
                        push=True,
                    ),
                    DEFAULT_TX_CONFIG,
                )
            ).transactions
```
