## Confirmation of Analog

The Apache mod_headers SSRF bug class (attacker-controlled data flowing into an outbound proxy destination that a trusted service will fetch) maps to Chia's DataLayer mirror mechanism: any wallet holder can create a "mirror" coin advertising arbitrary URLs for *any other user's* DataLayer store, and those URLs are later fetched by the subscribing user's/node's DataLayer service.

### Title
Unauthenticated Mirror-Coin Injection Enables SSRF Against DataLayer Client Nodes - (File: chia/data_layer/data_layer_wallet.py)

### Summary
`DataLayerWallet.create_new_mirror()` lets any wallet spend XCH to create a coin with the fixed `create_mirror_puzzle()` puzzle hash and free-form `urls` memo bound to an arbitrary `launcher_id` (store id) that the spender does not need to own. Any peer syncing that DataLayer store will treat this coin's memo URLs as legitimate mirror servers and issue outbound `aiohttp` HTTP requests to them, allowing SSRF against internal/cloud infrastructure of anyone running a DataLayer client.

### Finding Description
`create_new_mirror` builds a plain coin creation (not a singleton spend, no puzzle assertion tying the coin to the store's owner key): [1](#0-0) 
```
async def create_new_mirror(
    self, launcher_id: bytes32, amount: uint64, urls: list[bytes], action_scope, fee=uint64(0), ...
) -> None:
    await self.standard_wallet.generate_signed_transaction(
        amounts=[amount],
        puzzle_hashes=[create_mirror_puzzle().get_tree_hash()],
        ...
        memos=[[launcher_id, *(url for url in urls)]],
        ...
    )
```
`create_mirror_puzzle()` is a fixed, launcher-independent puzzle (`P2_PARENT.curry(Program.to(1))`), and the association between the mirror coin and a specific `launcher_id`/store lives *only* in the unauthenticated `memos` field: [2](#0-1) 
There is no check anywhere in `create_new_mirror`, `dl_new_mirror` RPC handler, or `DataLayer.add_mirror` that the caller owns, controls, or has any relationship to the target `launcher_id`'s singleton: [3](#0-2) [4](#0-3) 

Any other DataLayer client that is subscribed to (or later subscribes to) that `launcher_id` will pull all mirrors for the store and treat the attacker-supplied URLs as trusted server candidates: [5](#0-4) 
which populates `DataStore` subscriptions, and are subsequently used directly as HTTP request targets by `fetch_and_validate` → `insert_from_delta_file` → `download_file` → `http_download`, with no allow-listing, scheme restriction, or private-IP/loopback/link-local filtering: [6](#0-5) [7](#0-6) 

The `data-layer.md` internal notes independently confirm mirror/plugin URLs are treated as untrusted external inputs whose *downloaded content* is validated, but nowhere is the URL destination itself validated or scoped, and ownership of the mirror coin relative to the store is not enforced: [8](#0-7) [9](#0-8) 

### Impact Explanation
An unprivileged attacker (any wallet user who can afford a small XCH fee) can create a mirror coin tagged with a `launcher_id` belonging to a popular/well-known DataLayer store, embedding URLs such as `http://169.254.169.254/latest/meta-data/...` (cloud instance metadata), `http://localhost:<internal-port>/admin`, or other internal-only endpoints. Any node/operator that subscribes to that store (a normal, expected DataLayer client action) will have its local DataLayer service issue outbound HTTP GET/POST requests to attacker-chosen destinations — the classic SSRF impact: internal service reconnaissance, cloud credential theft via metadata endpoints, or triggering unintended side effects on internal-only HTTP APIs reachable from the victim's network position. This satisfies the "Medium/High" bar because it grants a remote, unauthenticated party control over outbound network destinations of a victim node, comparable in class/severity to the referenced Apache SSRF.

### Likelihood Explanation
Likelihood is high: creating a mirror coin only requires a standard coin spend with a tiny fee — no special permissions, no ownership of the target store, and no coordination with the store's actual owner are needed. The victim behavior (subscribing to a store and syncing mirrors) is the normal, documented DataLayer usage pattern, so exploitation does not require any unusual victim misconfiguration (unlike the Apache case, which explicitly requires an "unlikely" `mod_headers` configuration).

### Recommendation
- Require that `dl_new_mirror`/`create_new_mirror` verify the calling wallet owns the `launcher_id` singleton (or otherwise cryptographically bind mirror-coin creation to store ownership) before accepting it as an authoritative mirror source, or
- Treat all mirror URLs as fully untrusted network destinations at the HTTP-client layer: enforce scheme allow-listing (http/https only), resolve and block loopback/link-local/private/metadata IP ranges before connecting, and disallow redirects to such ranges, in `http_download` and the plugin `download_file` path (`chia/data_layer/download_data.py`).
- Consider requiring operator opt-in / an explicit trust list for mirror servers rather than automatically absorbing any on-chain-advertised mirror URL into `update_subscriptions_from_wallet`.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (or CLI `chia data add_mirror`) with `launcher_id` = the store id of a widely-subscribed DataLayer store the attacker does not own, and `urls=["http://169.254.169.254/latest/meta-data/iam/security-credentials/"]`, paying a small fee: [10](#0-9) 
2. The transaction confirms, creating a coin with puzzle hash `MIRROR_PUZZLE_HASH` and memo `[launcher_id, url]`.
3. A victim node subscribed to that `launcher_id` runs its periodic sync loop; `update_subscriptions_from_wallet` pulls this mirror via `dl_get_mirrors` and adds the URL to local subscriptions: [5](#0-4) 
4. On the next `fetch_and_validate` cycle, the victim's DataLayer service issues an outbound HTTP GET to the attacker-controlled URL via `http_download`: [7](#0-6) 
5. The attacker observes/exfiltrates the response (e.g., cloud metadata credentials) if the victim's DataLayer service runs in a cloud environment reachable via SSRF, or probes internal-only services the attacker could not otherwise reach directly.

**Uncertainty note:** I could not fully trace the `standard_wallet.generate_signed_transaction` path to confirm there is no additional server-side/mempool-level restriction rejecting arbitrary `launcher_id` memos for coins the sender doesn't control (this would require reviewing `chia/wallet/wallet.py`'s `generate_signed_transaction`, which was not returned in the search results). Based on the code paths retrieved, no such restriction appears to exist, but this should be verified directly in the full source before treating this as fully confirmed.

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

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L90-110)
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
    raise ValueError("The provided puzzle and solution do not create a mirror coin")
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

**File:** .cursor/context/data-layer.md (L26-28)
```markdown
- Do not treat a local root as current chain truth until wallet confirmation status has been reconciled.
- Do not treat mirror URLs, plugins, or static file names as trusted data sources.
- Do not collapse `None`, omitted root fields, and empty-root sentinels across RPC/service/wallet boundaries.
```

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```

**File:** chia/cmds/data_funcs.py (L270-285)
```python
async def add_mirror_cmd(
    rpc_port: int | None,
    store_id: bytes32,
    urls: list[str],
    amount: int,
    fee: uint64 | None,
    fingerprint: int | None,
) -> None:
    async with get_client(rpc_port=rpc_port, fingerprint=fingerprint) as (client, _):
        res = await client.add_mirror(
            store_id=store_id,
            urls=urls,
            amount=amount,
            fee=fee,
        )
        print(json.dumps(res, indent=2, sort_keys=True))
```
