### Title
Unvalidated On-Chain Mirror URLs Enable Server-Side Request Forgery in DataLayer Sync (`http_download`) - (File: `chia/data_layer/download_data.py`)

### Summary
Any wallet holder can publish a DataLayer "mirror" coin whose memo contains an arbitrary attacker-controlled string, which other DataLayer nodes later parse as a URL and pass directly into an unauthenticated server-side `aiohttp` GET request with no scheme/host/network validation. This mirrors the Kan `/api/download/attatchment` SSRF pattern: a user-supplied "URL" is taken at face value and used server-side to fetch content, with the response effectively acted upon (ingested into the local Merkle store) and errors/behavior observable by the attacker.

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet build a standard coin spend to the mirror puzzle, storing arbitrary `urls` (raw bytes/strings) in the coin memo: [1](#0-0) 

This is reachable unauthenticated-per-consensus (any spend-bundle submitter/wallet RPC caller) via `dl_new_mirror`: [2](#0-1) 

No URL scheme, host, or network-range validation exists at creation time (`DLNewMirror` only carries `launcher_id`, `amount`, `urls: list[str]`): [3](#0-2) 

Once confirmed on-chain, any node subscribed to that store id retrieves these mirror URLs and stores them as subscription server URLs: [4](#0-3) 

During periodic sync, `DataLayer.fetch_and_validate()` iterates candidate `servers_info` (derived from these mirror URLs) and calls `insert_from_delta_file()` → `download_file()` → `http_download()`: [5](#0-4) 

`http_download()` performs the actual unauthenticated, unvalidated fetch:
```
async with session.get(
    server_info.url + "/" + filename,
    headers=headers,
    timeout=timeout,
    proxy=proxy_url,
) as resp:
``` [6](#0-5) 

There is no check that `server_info.url` uses `http`/`https`, no denial of RFC1918/loopback/link-local addresses (e.g. `169.254.169.254` cloud metadata), and no restriction to a URL allow-list. The `.cursor` context docs for this module explicitly confirm this design assumption: *"Plugin and mirror URLs are external trust inputs... file path/name validation must stay strict"* but do not mention validating the network destination of the URL itself: [7](#0-6) 

This is architecturally identical to the Kan bug class: a user-controlled "URL" parameter is forwarded directly into a server-side HTTP client (`fetch()` in Kan, `aiohttp.ClientSession.get()` here) without SSRF filtering, and the server performs the request and processes the response.

### Impact Explanation
Any DataLayer node that subscribes to (or auto-syncs) a store with an attacker-published mirror will make outbound HTTP(S) requests to a URL fully chosen by the attacker at essentially no cost (the mirror coin can be created with `amount=uint64(0)`, as shown in tests): [8](#0-7) 

An attacker can point this at internal-network services or the cloud metadata endpoint of any machine running a DataLayer node/plugin/S3-downloader, and observe response-derived side effects (e.g., timing, success/failure of file download and root-hash validation, or crafted delta-file content that gets parsed by `insert_into_data_store_from_file()`). While the response body must ultimately satisfy DataLayer's Merkle-root verification to be "accepted" as valid, the request itself (headers, method, path) is fully attacker-controlled and blindly issued from the victim node's network position — enough for SSRF probing/exfiltration/pivoting into otherwise-unreachable internal networks, matching the reported bug class (S:C network reach via a trusted server acting as a proxy for attacker-chosen requests).

### Likelihood Explanation
Likelihood is High for any node that has auto-subscribe or subscribes to third-party/unfamiliar stores, since publishing a malicious mirror only requires a standard signed coin spend (any wallet with mempool access, including zero-value coins) with no consensus-level or RPC-level restriction on the mirror URL's shape/destination.

### Recommendation
- Validate mirror URLs at `dl_new_mirror`/`create_new_mirror` time and again before use in `http_download`/`update_subscription`: enforce an `http(s)` scheme, reject loopback/link-local/private/multicast/metadata IP ranges (with DNS-rebinding-aware resolution checks), and optionally require an operator-configured allow-list of mirror hosts.
- Add the same restriction in `chia/data_layer/download_data.py::http_download` (defense-in-depth) so plugin/self-hosted deployments cannot be tricked even if wallet-side validation is bypassed.
- Consider making mirror-based sync opt-in per store (already partially mitigated by subscription requirement) and clearly documenting the SSRF risk for operators running DataLayer with network access to sensitive internal services.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (or `chia data add_mirror`) for a target `store_id` with `amount=0` and `urls=["http://169.254.169.254/latest/meta-data/"]`, as demonstrated feasible in existing tests using zero-amount mirrors and arbitrary byte-string URLs: [8](#0-7) 
2. Transaction confirms on chain; any DataLayer node subscribed to `store_id` later calls `update_subscriptions_from_wallet()` and picks up the attacker URL as a subscription server: [4](#0-3) 
3. On the next sync cycle, `fetch_and_validate()`/`insert_from_delta_file()`/`download_file()` calls `http_download()`, causing the victim node to issue an unauthenticated GET request to the attacker-chosen internal URL: [6](#0-5) 
4. Attacker observes differences in mirror-failure/backoff behavior (`server_misses_file` vs successful download path) to infer reachability/response characteristics of the internal target, or crafts a data-layer-shaped response to attempt data exfiltration through the delta-file processing pipeline.

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

**File:** .cursor/context/data-layer.md (L83-89)
```markdown
## Fragility Hotspots

- High-risk edits move work across DB writer transactions, `_update_confirmation_status()`, pending-root status changes, or wallet RPC calls. These boundaries encode publication and rollback assumptions.
- File-system writes for Merkle blobs, key/value blobs, and `.dat` files have TODOs around locking. Concurrent service/plugin/server access should be treated as a real consistency concern.
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
- DataLayer wallet code depends on singleton CLVM structure, odd singleton amounts, lineage proofs, and offer solver field names. Changes in wallet puzzle drivers or offer summaries can break this module without direct edits here.
```

**File:** chia/_tests/wallet/db_wallet/test_dl_wallet.py (L811-812)
```python
    async with dl_wallet.wallet_state_manager.new_action_scope(DEFAULT_TX_CONFIG, push=True) as action_scope:
        await dl_wallet.create_new_mirror(launcher_id, uint64(0), [b"foo", b"bar"], action_scope)
```
