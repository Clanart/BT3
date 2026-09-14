### Title
DataLayer mirror URLs allow unauthenticated on-chain SSRF against subscriber nodes - ([File: chia/data_layer/download_data.py])

### Summary
Any wallet holder can attach arbitrary, completely unvalidated URL strings to an on-chain DataLayer "mirror" coin via `create_new_mirror`/`dl_new_mirror`. Every other node that subscribes to (tracks) that DataLayer store will later use that attacker-supplied URL as the target of an outbound HTTP GET, with no scheme/host/IP validation, exactly the class of bug described in the FastChat SSRF advisory (unvalidated path/target used to build an outbound request that can reach internal networks or the cloud metadata endpoint).

### Finding Description
`DataLayerWallet.create_new_mirror()` creates a mirror coin whose puzzle hash is `create_mirror_puzzle()` and stores the caller-supplied `urls: list[bytes]` directly as coin memos, with no validation of URL scheme or destination: [1](#0-0) 

`WalletRpcApi.dl_new_mirror` is a standard, unprivileged transaction endpoint (`tx_endpoint=True, auto_push=True`) that any local wallet RPC caller (or, in a shared/hosted wallet context, any user with wallet access) can invoke to publish this on-chain: [2](#0-1) [3](#0-2) 

When a mirror coin for a tracked launcher is seen, `DataLayerWallet.coin_added()` decodes and stores these URLs verbatim (no validation) into `dl_store`: [4](#0-3) 

The DataLayer service later pulls these mirror URLs into local subscription server lists via `update_subscriptions_from_wallet()`: [5](#0-4) 

During periodic sync, `fetch_and_validate()` picks one of these `ServerInfo` entries and calls `insert_from_delta_file()` → `download_file()` → `http_download()`: [6](#0-5) 

`http_download()` in `chia/data_layer/download_data.py` builds the outbound request directly from the attacker-controlled `server_info.url` with no scheme allowlist, no private/link-local IP blocking, and no restriction against cloud metadata addresses (e.g. `169.254.169.254`) or `localhost`: [7](#0-6) 

The only "validation" is `is_filename_valid()`, which checks the *filename* format, not the URL/host that the request is sent to: [8](#0-7) 

I searched for any URL scheme/host validation logic in the DataLayer module (`urlparse`, private-IP checks, scheme allowlists) and found none in `data_layer.py`, `data_layer_wallet.py`, or `download_data.py`; the only `urlparse` usage in this subsystem is in the operator-configured `s3_plugin_service.py`, which is not on this path.

### Impact Explanation
Any unprivileged wallet user who is able to create a DataLayer mirror coin (a normal, cheap, unauthenticated on-chain spend) can cause every other node that subscribes to that store's launcher id to issue outbound HTTP GET requests to an attacker-chosen destination — including internal-network hosts, `localhost` services on the victim machine, or cloud metadata endpoints (`http://169.254.169.254/...`) if the victim node runs in a cloud VM. This matches CWE-918 SSRF and can lead to disclosure of instance credentials/secrets or unauthorized access to internal services from any node that syncs the affected DataLayer store — a real security impact reachable purely through a submitted spend bundle and subsequent DataLayer subscription behavior, without needing malicious peers, node compromise, or operator error.

### Likelihood Explanation
Likelihood is high: `dl_new_mirror` is a standard wallet RPC endpoint requiring only a small amount of XCH and a fee, no special permissions beyond normal wallet access, and mirror URLs are never validated at creation, coin-tracking, or download time. Any node operator who subscribes to (or owns, since owned stores also process update/subscription flows) the targeted DataLayer store will trigger the outbound request automatically during its periodic `update_subscription()`/`fetch_and_validate()` cycle.

### Recommendation
Validate mirror URLs before persisting/using them for outbound requests:
- Restrict accepted schemes (e.g., `http`/`https` only, reject `file://`, `s3://` outside the plugin path, etc.).
- Resolve and reject destinations that resolve to loopback, link-local (including `169.254.169.254`), private, or multicast address ranges unless explicitly allowed by configuration.
- Apply this validation both when a mirror coin is first observed (`DataLayerWallet.coin_added()` / `create_new_mirror()`) and defensively again in `http_download()`/`download_file()` before making the request, since URLs can also arrive through subscription config rather than only mirror coins.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (via `WalletRpcApi.dl_new_mirror`) for a `launcher_id` of a DataLayer store that a victim node tracks, with `urls=["http://169.254.169.254/latest/meta-data/iam/security-credentials/"]` and a minimal `amount`/`fee`.
2. Once the mirror coin confirms on chain, the victim's `DataLayerWallet.coin_added()` records the URL for that launcher without validation, and `DataLayer.update_subscriptions_from_wallet()` pulls it into the store's subscription server list.
3. On the victim node's next `fetch_and_validate()` cycle, `insert_from_delta_file()` → `download_file()` → `http_download()` issues `session.get("http://169.254.169.254/latest/meta-data/iam/security-credentials/" + "/" + filename, ...)`, causing the victim node (if cloud-hosted) to leak instance credentials or, in general, to probe/interact with internal-only services on behalf of the attacker.

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

**File:** chia/wallet/wallet_rpc_metadata.py (L642-648)
```python
    WalletRpcMetadata(
        endpoint_name="dl_new_mirror",
        request_type=wallet_request_types.DLNewMirror,
        response_type=wallet_request_types.DLNewMirrorResponse,
        tx_endpoint=True,
        auto_push=True,
    ),
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

**File:** chia/data_layer/download_data.py (L28-59)
```python
def is_filename_valid(filename: str, group_by_store: bool = False) -> bool:
    if group_by_store:
        if filename.count("/") != 1:
            return False
        filename = filename.replace("/", "-")

    split = filename.split("-")

    try:
        raw_store_id, raw_node_hash, file_type, raw_generation, raw_version, *rest = split
        store_id = bytes32(bytes.fromhex(raw_store_id))
        node_hash = bytes32(bytes.fromhex(raw_node_hash))
        generation = int(raw_generation)
    except ValueError:
        return False

    if len(rest) > 0:
        return False

    # TODO: versions should probably be centrally defined
    if raw_version != "v1.0.dat":
        return False

    if file_type not in {"delta", "full"}:
        return False

    generate_file_func = get_delta_filename if file_type == "delta" else get_full_tree_filename
    reformatted = generate_file_func(
        store_id=store_id, node_hash=node_hash, generation=generation, group_by_store=False
    )

    return reformatted == filename
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
