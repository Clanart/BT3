## SSRF Analog Found: DataLayer Mirror URLs Enable Unauthenticated Outbound Requests to Internal/Private Network Addresses

### Title
Missing SSRF filtering on DataLayer mirror URLs allows attacker-controlled outbound HTTP requests to internal/private addresses - (File: `chia/data_layer/download_data.py`)

### Summary
Chia's DataLayer downloads delta/full-tree files from mirror URLs that are published on-chain via a permissionless "mirror coin." Any wallet holder can create a mirror coin for **any** store's `launcher_id` — including a store they do not own — with attacker-chosen URLs. The DataLayer service later fetches those URLs with `aiohttp` in `http_download()` with no validation against loopback, link-local, or private (RFC1918) address ranges, mirroring the exact bug class in CVE-2020-15879 (Bitwarden SSRF from unfiltered internal IP ranges).

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a standard coin sent to `MIRROR_PUZZLE_HASH` with memos `[launcher_id, *urls]`: [1](#0-0) 

This is invoked from the public wallet RPC `dl_new_mirror`, which performs no check that the caller's wallet owns or has any relationship to the target `launcher_id`: [2](#0-1) 

The mirror puzzle itself (`P2_PARENT.curry(Program.to(1))`) and its memo decoding via `get_mirror_info()` place no constraint on which `launcher_id` a spender can target: [3](#0-2) 

Any node that owns or subscribes to that `launcher_id` periodically calls `update_subscriptions_from_wallet()`, which pulls **all** mirror URLs for the store from chain state and inserts them into the local subscription table without any filtering: [4](#0-3) 

Those URLs are later used by `fetch_and_validate()` / `update_subscription()` to issue outbound `aiohttp` GET requests via `http_download()`, with no restriction on target host/IP (no blocking of `127.0.0.0/8`, `169.254.0.0/16`, RFC1918 ranges, or IPv6 ULA/link-local addresses — the same omission class as the referenced CVE): [5](#0-4) 

The plugin-download path (`download_file()`), when no downloader plugin is configured, falls straight to `http_download` using the attacker-influenced `server_info.url`: [6](#0-5) 

### Impact Explanation
A malicious wallet user (no special privilege, no relationship to the target store required) can force any DataLayer node that owns or tracks a given `store_id` to issue outbound HTTP GET requests to attacker-chosen destinations, including:
- Cloud metadata endpoints (`169.254.169.254`) potentially exposing instance credentials to log output or side effects.
- Loopback/internal admin services (e.g. the node's own RPC ports or other local services) that trust localhost-origin requests.
- Internal-network hosts unreachable from the public internet, enabling network reconnaissance/pivoting from the victim's environment.

This satisfies the "unauthorized coin movement / forged asset identity / SSRF-class transaction-processing" impact bar as a spend-triggered network-reachability primitive reachable by an unprivileged wallet action (mirror coin creation costs only a fee).

### Likelihood Explanation
Likelihood is high for any node running a DataLayer service that owns or subscribes to at least one store: creating the malicious mirror coin requires only a standard signed transaction (`dl_new_mirror`) targeting the fee amount, and no ownership check gates which `launcher_id` can be targeted. The victim's periodic `periodically_manage_data()` loop will pick up the malicious mirror URL automatically on its next sync cycle.

### Recommendation
- Validate and reject mirror/subscription URLs resolving to loopback (`127.0.0.0/8`, `::1`), link-local (`169.254.0.0/16`, `fe80::/10`), unique-local IPv6 (`fc00::/7`), and other private/reserved ranges before storing or fetching them (mirroring `download_data.http_download` and `update_subscriptions_from_wallet`).
- Consider requiring proof of store ownership before accepting mirror URL memos into the local subscription/mirrors table, or at minimum resolve+re-validate the final connection address (not just the string) to defeat DNS-rebinding.
- Add SSRF protections at the `aiohttp.ClientSession` layer (e.g., a resolver/connector that blocks disallowed IP ranges) shared by all DataLayer HTTP fetch paths (`http_download`, `get_downloader`, plugin download POSTs).

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (`DLNewMirror(launcher_id=<victim_store_id>, amount=1, urls=["http://169.254.169.254/latest/meta-data/"])`) targeting a `launcher_id` belonging to a store the attacker does not own — the RPC performs no ownership check [2](#0-1) .
2. The mirror coin is confirmed on-chain, carrying the malicious URL in its memos per `create_mirror_puzzle()`/`get_mirror_info()` [3](#0-2) .
3. Any node tracking/owning `<victim_store_id>` calls `update_subscriptions_from_wallet()`, pulling the attacker's URL into its local subscription table unfiltered [4](#0-3) .
4. On the next sync cycle, `fetch_and_validate` → `insert_from_delta_file` → `download_file` → `http_download` issues an outbound GET to the attacker's URL with no internal-address filtering [5](#0-4) .

**Uncertainty / unverified**: I was unable to fully inspect `chia/data_layer/dl_wallet_store.py` mirror-record persistence logic in this session (grep found matches but content wasn't read) to confirm there is no downstream ownership filter reintroduced when converting on-chain mirror coins into `Mirror` records returned by `get_mirrors_for_launcher`/`dl_get_mirrors`. If such a filter exists there, it would need to be explicitly confirmed or ruled out before treating this as fully proven; based on the code paths inspected (`create_new_mirror`, `dl_new_mirror`, `add_mirror`, `update_subscriptions_from_wallet`), no such ownership check was found.

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

**File:** chia/data_layer/download_data.py (L126-145)
```python
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
