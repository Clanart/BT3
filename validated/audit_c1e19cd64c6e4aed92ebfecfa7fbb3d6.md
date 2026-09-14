## Analog Confirmed: Blind SSRF via DataLayer Mirror URLs

### Title
Attacker-controlled DataLayer mirror URLs enable blind Server-Side Request Forgery against DataLayer nodes and downloader plugins - (File: `chia/data_layer/data_layer_wallet.py`, `chia/data_layer/data_layer.py`, `chia/data_layer/download_data.py`)

### Summary
Any wallet user can create a DataLayer "mirror" coin naming **any** existing DataLayer store's `launcher_id` together with arbitrary attacker-chosen URL strings, paying only the mirror amount and a fee from their own funds. Any node that tracks that `launcher_id` (owner or subscriber) will automatically ingest those URLs into its subscription/mirror table and periodically issue outbound HTTP(S) requests to them from the DataLayer service process — a blind SSRF primitive analogous to Pi-hole's `gravity_DownloadBlocklistFromUrl()` issue, where unvalidated remote URLs are fetched server-side.

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a standard coin spend whose memos encode `[launcher_id, *urls]` with **no validation of the URL contents, scheme, or host**, and no requirement that the caller owns or has any relationship to `launcher_id`: [1](#0-0) 

When the resulting mirror coin is farmed, every wallet that tracks that `launcher_id` observes it in `coin_added()`, decodes the attacker-supplied URLs via `get_mirror_info()`, and persists them verbatim into the local `mirrors` table: [2](#0-1) 

`get_mirror_info()` simply extracts memos from the coin's create-coin condition with no URL sanitization: [3](#0-2) 

The DataLayer service then pulls these URLs unconditionally from wallet RPC into its own subscription table for any store it is subscribed to or owns: [4](#0-3) 

During the periodic sync loop, `fetch_and_validate()` selects one of these attacker-controlled URLs and calls `insert_from_delta_file()` → `download_file()`. If no plugin is configured, `http_download()` performs a raw `aiohttp` GET directly to `server_info.url + "/" + filename` with no allow-list, no scheme restriction, and no blocking of private/link-local/metadata address ranges: [5](#0-4) 

If a downloader plugin is configured (e.g., the S3 plugin), the attacker URL is instead forwarded server-to-server by POSTing it to the plugin's `/download` endpoint, extending the SSRF surface to a second internal service: [6](#0-5) 

Nowhere in this chain — `create_new_mirror`, `get_mirror_info`, `coin_added`, `update_subscriptions_from_wallet`, `subscribe`, or `http_download` — is the URL validated against a scheme allow-list or checked against internal/loopback/link-local address ranges, which the codebase's own DataLayer context notes as an explicit trust boundary that must be respected ("Plugin and mirror URLs are external trust inputs").

### Impact Explanation
This is a genuine blind SSRF: an unprivileged attacker who merely knows a target `launcher_id` (public, since store ids are on-chain) can force any node tracking that store — including its owner and every subscriber — to issue outbound requests to attacker-chosen destinations (e.g., internal admin RPC ports, cloud metadata endpoints such as `169.254.169.254`, or other internal-network services) purely by paying a small XCH mirror fee. Depending on network placement, this can be used for internal network reconnaissance, interacting with unauthenticated internal services, or triggering downstream RCE if an internal service reacts unsafely to attacker-controlled requests/content — directly mirroring the CVE-2024-34361 bug class where an unvalidated user-supplied URL is fetched server-side.

### Likelihood Explanation
Likelihood is high for any environment running the DataLayer service with mirrors/subscriptions enabled: creating a mirror coin requires only a standard signed spend of the attacker's own coins (no special privilege, no ownership of the target store), and the ingestion/sync path is automatic and unauthenticated with respect to URL content.

### Recommendation
Validate mirror/subscription URLs at ingestion (`coin_added` / `update_subscriptions_from_wallet`) and before every outbound fetch (`http_download`, plugin `download_file` request): enforce an `http`/`https` scheme allow-list, resolve and reject requests targeting loopback, link-local (including `169.254.169.254`), private, and other non-routable address ranges, and consider requiring operator opt-in/allow-listing of mirror hosts before they are dialed automatically by the background sync loop.

### Proof of Concept
1. Attacker identifies a target `launcher_id` for a DataLayer store that a victim node owns or subscribes to (store ids are public on-chain).
2. Attacker calls `create_new_mirror(launcher_id=<victim_store_id>, amount=1, urls=[b"http://169.254.169.254/latest/meta-data/"], ...)` via their own wallet (see test pattern in `chia/_tests/wallet/db_wallet/test_dl_wallet.py:572-575`), paying a small fee.
3. Once farmed, the victim's `DataLayerWallet.coin_added()` records the mirror with the malicious URL because it tracks `launcher_id`.
4. The victim's `DataLayer.update_subscriptions_from_wallet()` copies the URL into local subscriptions.
5. On the next `periodically_manage_data()` cycle, `fetch_and_validate()`/`http_download()` issues an outbound GET from the victim's DataLayer process to `http://169.254.169.254/latest/meta-data/...`, demonstrating server-side request forgery triggered entirely by an unprivileged, unrelated wallet user.

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

**File:** chia/data_layer/download_data.py (L147-165)
```python
    log.info(f"Using downloader {downloader} for store {store_id.hex()}.")
    request_json = {
        "url": server_info.url,
        "client_folder": str(client_foldername),
        "filename": filename,
        "group_files_by_store": group_downloaded_files_by_store,
        "max_delta_file_size": max_delta_file_size,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                downloader.url + "/download",
                json=request_json,
                headers=downloader.headers,
                timeout=timeout,
            ) as response:
                res_json = await response.json()
                assert isinstance(res_json["downloaded"], bool)
                return res_json["downloaded"]
```

**File:** chia/data_layer/download_data.py (L298-319)
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
```
