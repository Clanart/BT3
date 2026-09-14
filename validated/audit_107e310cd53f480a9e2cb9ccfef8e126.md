### Title
Data Layer mirror/subscription URL fetching lacks SSRF protections (no private-IP/loopback filtering on outbound HTTP downloads) - ([File: chia/data_layer/download_data.py])

### Summary
CVE-2020-8138 describes Nextcloud's calendar-subscription feature performing outbound HTTP fetches against attacker-supplied URLs without validating that the resolved address is not an internal/private (IPv4-in-IPv6) address, enabling SSRF. Chia's Data Layer has an analogous pattern: any wallet can publish an on-chain "mirror" containing arbitrary URL strings for **any** store id, and any node subscribed to (or auto-tracking) that store will fetch those URLs over plain HTTP with no destination-address validation.

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet spend a coin to publish a `create_mirror_puzzle()` coin carrying `launcher_id` plus arbitrary URL memos, with no check that the caller owns or controls that `launcher_id`/store: [1](#0-0) 

The RPC surface (`dl_new_mirror` / `/add_mirror`) forwards these attacker-controlled URL strings unchanged: [2](#0-1) [3](#0-2) 

A DataLayer service that tracks a store pulls these mirror URLs directly from wallet RPC and stores them as subscription server URLs with no scheme/host allow-list or private-network filtering: [4](#0-3) 

During the periodic sync loop, `fetch_and_validate()` randomly selects one of these server URLs and issues an HTTP request to it via `insert_from_delta_file()` → `download_file()` → `http_download()`: [5](#0-4) [6](#0-5) 

Nowhere in this path (mirror creation, RPC ingestion, subscription storage, or the HTTP download call) is there IP/host validation analogous to rejecting loopback, link-local, private, or IPv4-mapped-IPv6 addresses — the only URL-shape validation in the module (`is_filename_valid()`) checks downloaded *filenames*, not the destination host: [7](#0-6) 

This mirrors the CVE-2020-8138 bug class: an attacker-controlled "subscription"/mirror URL is fetched by the victim's server process without SSRF-safe address filtering.

### Impact Explanation
A malicious peer/counterparty can publish a mirror for a store id (their own or someone else's, since ownership is not enforced) pointing to `http://127.0.0.1:<port>/...` or an internal RFC1918/link-local address. Any DataLayer node that later subscribes to or auto-tracks that store id (e.g. via `update_subscriptions_from_wallet` reading on-chain mirrors, or a user manually subscribing to a URL they were told to use, similar to "subscribing to a malicious calendar URL") will have its own DataLayer service issue outbound HTTP requests to that internal address as part of `periodically_manage_data()` / `fetch_and_validate()`. This can be used to probe or interact with services on the victim's localhost/internal network (e.g. other local RPC ports, cloud metadata endpoints) — a classic SSRF impact.

### Likelihood Explanation
Reaching this requires only: (1) creating an on-chain mirror coin with attacker-chosen URL memos (any funded wallet can do this — no privileged access, no ownership check on the target launcher_id), and (2) a victim node subscribing to or tracking that store, which is a normal DataLayer workflow (`dl_track_new`/`subscribe`, or automatic mirror discovery via `update_subscriptions_from_wallet`). No cryptographic breakage or race condition is required, only a standard RPC call plus normal DL sync behavior.

### Recommendation
Add SSRF-safe URL/host validation before any DataLayer HTTP fetch: resolve the hostname, reject loopback/link-local/private/multicast/IPv4-mapped-IPv6 destinations (using the same style of check already present in `chia/util/network.py`'s `is_in_network`/`is_localhost` for peer connections), and apply this validation both when accepting mirror URLs from wallet RPC (`update_subscriptions_from_wallet`) and immediately before `http_download()` issues the request in `download_data.py`. Also consider requiring store ownership (or explicit user opt-in) before auto-adding wallet-discovered mirror URLs as fetch targets.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (or CLI `chia data add_mirror`) with `launcher_id` set to a victim's store id and `urls=["http://127.0.0.1:<internal_port>/admin"]`; no ownership check blocks this — see `create_new_mirror()` / `dl_new_mirror` above.
2. The mirror coin confirms on-chain.
3. A victim DataLayer node that subscribes to or already tracks that `store_id` calls `update_subscriptions_from_wallet()`, pulling the malicious URL into its local subscription/server table (`chia/data_layer/data_layer.py:979-985`).
4. On the next `periodically_manage_data()` cycle, `fetch_and_validate()` selects that server and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, causing the victim process to issue an HTTP GET to `http://127.0.0.1:<internal_port>/admin` (or any other internal address chosen by the attacker) with no destination filtering.

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

**File:** chia/data_layer/data_layer_rpc_api.py (L468-475)
```python
    async def add_mirror(self, request: dict[str, Any]) -> EndpointResult:
        store_id = request["id"]
        id_bytes = bytes32.from_hexstr(store_id)
        urls = request["urls"]
        amount = request["amount"]
        fee = get_fee(self.service.config, request)
        await self.service.add_mirror(id_bytes, urls, amount, fee)
        return {}
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
