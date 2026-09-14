Confirmed: `chia/data_layer/download_data.py`'s `http_download()` performs `session.get(server_info.url + "/" + filename, ...)` with no scheme/host/IP validation anywhere in `chia/data_layer/*.py` (only `s3_plugin_service.py` and `start_data_layer.py` reference `urlparse`, and only to check `scheme == "s3"`, not to block private/internal targets). The `server_info.url` values originate from `Mirror` records that `DataLayerWallet.coin_added()` extracts on-chain from arbitrary spend memos and stores unfiltered via `DataLayerStore.add_mirror()`.

### Title
Server-Side Request Forgery via unvalidated DataLayer mirror URLs - (File: chia/data_layer/download_data.py)

### Summary
Any wallet user can publish an arbitrary URL on-chain as a "mirror" for a DataLayer store by spending a coin to the mirror puzzle with the URL encoded in the memo, via `DataLayerWallet.create_new_mirror()` [1](#0-0) . Any other node that is subscribed to (or later subscribes to) that store id will, in its normal background sync loop, fetch these attacker-chosen URLs and issue outbound HTTP GET requests to them with no scheme, host, or IP restriction, resulting in unauthenticated SSRF against the DataLayer service host.

### Finding Description
`DataLayerWallet.coin_added()` recognizes coins sent to `create_mirror_puzzle().get_tree_hash()`, decodes `launcher_id` and `urls` straight from the parent spend's puzzle reveal/solution, and persists them unvalidated via `DataLayerStore.add_mirror()` when the launcher is tracked: [2](#0-1) . There is no validation of the URL's scheme, host, or target IP address anywhere in this ingestion path, in `dl_wallet_store.py`'s `add_mirror()`, or later when the DataLayer service turns these mirrors into subscription servers in `update_subscriptions_from_wallet()`: [3](#0-2) .

During the periodic sync loop, `fetch_and_validate()` picks a mirror server URL and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which performs `aiohttp` `session.get(server_info.url + "/" + filename, ...)` directly against the attacker-supplied URL with no allow-list, scheme check, or private/internal IP filtering: [4](#0-3) . The only mitigating condition is that the target must respond with a plausible delta filename and content, but the GET request itself (including to internal RPC ports, `169.254.169.254` cloud metadata, or other internal-only services) is issued unconditionally before any content validation occurs.

Any wallet user can create such a mirror for a minimal fee/amount, and this requires no special privilege beyond normal DataLayer wallet usage — mirroring the report's root cause of "passes the user-supplied URL directly to requests without host or IP validation," except here the "unauthenticated remote attacker" is any DataLayer/wallet participant who can publish a mirror coin, and the victim is any peer node subscribed to that store.

### Impact Explanation
A malicious DataLayer participant can force any node that subscribes to (or already has mirrors registered for) their store to make outbound HTTP requests to arbitrary internal or cloud-internal endpoints on a schedule (the periodic `update_subscription()`/`fetch_and_validate()` loop), enabling internal network reconnaissance/port scanning from the victim's infrastructure, interaction with cloud metadata services (potential credential leakage if responses were ever reflected back, though DataLayer expects binary delta content), and request forgery against internal-only services reachable from the DataLayer node. This does not directly move funds or forge on-chain assets, but it is a genuine unauthenticated SSRF primitive reachable purely through normal DataLayer usage by an unprivileged participant.

### Likelihood Explanation
High reachability: publishing a mirror coin is a normal, low-cost DataLayer wallet operation (`dl_new_mirror`/`add_mirror`), requiring no special permissions, and any peer that tracks/subscribes to the corresponding launcher id will automatically fetch and register these URLs and periodically attempt HTTP GETs against them without any host/IP validation.

### Recommendation
Validate mirror URLs before persisting/using them: enforce an allow-list of schemes (e.g., `http`/`https` only), resolve and reject requests targeting private/loopback/link-local/metadata IP ranges (e.g., using `ipaddress` checks similar in spirit to the `scheme == "s3"` check already used in `s3_plugin_service.py`), and re-validate at request time in `http_download()` in `chia/data_layer/download_data.py` immediately before each `session.get()` call, not just when displaying mirror lists.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror`/`dl_new_mirror` with `urls=["http://169.254.169.254/latest/meta-data/"]` (or `http://<victim-internal-host>:<port>/...`), as shown in `test_mirrors` [5](#0-4) .
2. A victim node subscribes to the attacker's `store_id` (a normal DataLayer client action).
3. The victim's background `periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()` loop reads the mirror URL and calls `http_download()`, which issues an unauthenticated `aiohttp` GET request to the attacker-controlled/internal target: [6](#0-5) .
4. The victim's DataLayer process has now made an outbound request to an arbitrary internal/cloud-metadata endpoint chosen by the attacker.

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
