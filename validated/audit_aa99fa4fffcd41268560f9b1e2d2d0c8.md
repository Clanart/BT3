## Title
Unauthenticated On-Chain Mirror URLs Cause Server-Side Request Forgery in DataLayer Sync (No Destination Validation on `http_download`) - (File: `chia/data_layer/download_data.py`)

### Summary
Chia's DataLayer mirror mechanism lets *any* wallet on the network attach an arbitrary URL string to *any other* store's `launcher_id` by spending a small/zero-value coin. Any node subscribed to that store automatically ingests the URL and later has its DataLayer service open an outbound HTTP connection to it (via `http_download`) to fetch delta files, with no validation of the resolved destination address — directly analogous to the Hugo `security.http.urls` SSRF (CVE-2026-10582), where a URL allowlist/text check exists but the actual outbound connection is never checked against loopback/private/link-local/metadata address ranges.

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a "mirror" coin whose puzzle is the generic `create_mirror_puzzle()` (`P2_PARENT.curry(Program.to(1))`), with the target `launcher_id` and URL list simply placed in the `CREATE_COIN` memo: [1](#0-0) 

Nothing constrains `launcher_id` to be a store the spender owns — the RPC layer (`dl_new_mirror`) passes the caller-supplied `launcher_id`/`urls` straight through without an ownership check: [2](#0-1) 

When any wallet observes a new mirror-puzzle coin, it decodes the memo and, as long as the *target* `launcher_id` is a store the local wallet is tracking (which can simply mean "subscribed to", not "owned"), unconditionally records the attacker-chosen URL as a mirror for that store: [3](#0-2) 

`DataLayer.update_subscriptions_from_wallet()` pulls these mirror URLs from the wallet and merges them into the local subscription list used for syncing, with no filtering: [4](#0-3) 

`fetch_and_validate()` then randomly selects one of these servers and performs an HTTP download (`http_download`) or hands the URL to a downloader plugin, again with no address validation before connecting: [5](#0-4) [6](#0-5) 

This mirrors the Hugo bug class precisely: the "allowlist" here (`is_launcher_tracked`, filename validation via `is_filename_valid`) only checks *identity of the store*, never resolves the *destination host* of the URL or checks it against loopback/private/link-local/cloud-metadata ranges. There is no dial-time hook or DNS-resolution check anywhere in `download_data.py` or `data_layer.py` before the aiohttp client connects.

### Impact Explanation
Any unprivileged wallet user (or Data Layer client) can publish an on-chain mirror coin pointing an arbitrary victim `launcher_id` at `http://169.254.169.254/...`, `http://127.0.0.1:<internal-port>/...`, or an internal-network address. Every node that subscribes to that store — a routine, low-privilege Data Layer client action — will have its DataLayer service issue outbound GET requests to that address as part of `fetch_and_validate()`/`http_download()`. Response handling then treats the body as delta-file content fed into `insert_into_data_store_from_file()`; even if content validation ultimately rejects malformed data, the SSRF itself (an internal-network probe/exfiltration channel via a background service that connects on the operator's behalf, potentially reaching cloud metadata services or internal HTTP endpoints) is complete before that content check occurs. This satisfies the reachable/attacker-controlled criteria: a low-privilege actor (anyone able to submit an on-chain mirror-coin spend and any Data Layer client that subscribes) can force victim nodes to make attacker-chosen outbound HTTP requests to internal/private targets.

### Likelihood Explanation
Likelihood is Medium: it requires (a) publishing a mirror coin for the target `launcher_id` (a cheap, unprivileged spend that any wallet can create, even with amount 0), and (b) a victim node subscribing to that store id, which is a normal, expected DataLayer client action (subscriptions are commonly created to mirror/replicate public stores). No signature over the target beyond the coin owner's own signature is required, and no relationship to the actual store owner is enforced.

### Recommendation
- Restrict `DataLayerWallet.create_new_mirror`/`dl_new_mirror` so only the store owner (or an explicitly authorized signer for that `launcher_id`) can register mirrors, or at minimum require an on-chain authorization proof tied to the singleton.
- Before any HTTP fetch in `download_data.py`/`data_layer.py`, resolve the URL's hostname and reject destinations that resolve to loopback, private, link-local, multicast, or cloud-metadata address ranges (e.g., re-check after each redirect too), mirroring the fix needed in the Hugo advisory (validate the address actually dialed, not just the URL text).
- Apply the same destination-address validation to `PluginRemote`/downloader/uploader URLs (`get_downloader`, `s3_plugin_service.py` handlers) since these are also driven by externally-supplied mirror data.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (RPC or CLI `chia data add_mirror`) with `launcher_id` = victim's store id and `urls=["http://169.254.169.254/latest/meta-data/"]`, `amount=0`, paying only the fee: [7](#0-6) 
2. The mirror coin is created and confirmed on chain via `create_new_mirror`.
3. Victim node, already subscribed to the same `launcher_id` (a normal, expected DataLayer client action), picks up the new mirror through `coin_added`/`get_mirrors_for_launcher` and merges it into subscription servers via `update_subscriptions_from_wallet`.
4. On the victim's next `periodically_manage_data()` cycle, `fetch_and_validate()` selects the malicious server and calls `insert_from_delta_file` → `download_file` → `http_download`, causing the victim's DataLayer service to issue an outbound GET to `169.254.169.254` (or any internal address chosen by the attacker) with no destination-address validation.

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

**File:** chia/data_layer/data_layer.py (L642-690)
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

**File:** chia/wallet/wallet_request_types.py (L1853-1859)
```python
@streamable
@dataclass(frozen=True, kw_only=True)
class DLNewMirror(TransactionEndpointRequest):
    launcher_id: bytes32
    amount: uint64
    urls: list[str]

```
