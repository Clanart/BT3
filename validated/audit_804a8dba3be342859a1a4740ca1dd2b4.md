## Title
Server-Side Request Forgery via unauthenticated DataLayer mirror URLs — (`chia/data_layer/data_layer_wallet.py`, `chia/data_layer/download_data.py`, `chia/data_layer/data_layer.py`)

## Summary
Chia DataLayer lets any wallet holder publish "mirror" server URLs for **any** store (launcher id), including stores they do not own. Those attacker-chosen URLs are later fetched automatically by the outbound HTTP client of every other node's DataLayer service that subscribes to that store, with no validation of scheme, host, or destination (no blocking of loopback/link-local/internal addresses). This is the same bug class as CVE-2024-5526 (Grafana OnCall webhook SSRF): an unprivileged actor supplies a URL that is later fetched server-side by another party's service.

## Finding Description
`DataLayerWallet.create_new_mirror()` builds a standard coin spend that stores the caller-supplied `urls` in the coin memo, keyed to an arbitrary `launcher_id`, with **no check that the caller owns/controls that launcher/store**: [1](#0-0) 

This is exposed directly via the wallet RPC `dl_new_mirror`, which likewise performs no ownership or URL validation before pushing the transaction: [2](#0-1) 

Any wallet user (an unprivileged spend-bundle submitter, just needs to pay the mirror coin amount + fee) can therefore publish arbitrary mirror URLs (e.g., `http://169.254.169.254/latest/meta-data/`, `http://127.0.0.1:<internal-port>/`, or an internal-network address) against a **popular/public store id used by other users**, not just their own store.

Other nodes' `DataLayer` service periodically pulls mirror URLs from the wallet and stores them as subscription server info without any URL/host validation: [3](#0-2) 

During the sync loop, `fetch_and_validate()` iterates these attacker-controlled URLs and issues outbound HTTP requests to them via `insert_from_delta_file()` → `download_file()` → `http_download()`: [4](#0-3) [5](#0-4) 

`http_download()`/`aiohttp` performs the request with no scheme allow-list and no blocking of internal/loopback/link-local destinations — the URL is only checked for filename shape after the fact (`is_filename_valid`), not for SSRF-relevant host/scheme restrictions.

The same pattern also affects `get_downloader()`/`get_uploaders()`/`check_plugins()`, but those operate on operator-configured plugin URLs rather than the on-chain, attacker-supplied mirror URLs, which is the more severe path here.

## Impact Explanation
A victim's DataLayer node, once subscribed to a store that has received a malicious mirror coin from any third party, will make outbound HTTP `POST`/download requests to attacker-chosen hosts/ports (cloud metadata endpoints, internal-only admin services, local ports on the node's own host, etc.) with node-controlled network reachability. This can be used to probe/attack internal infrastructure reachable from the node, exfiltrate metadata-service credentials in cloud deployments, or interact with local-only services bound to the node's host. This is a Server-Side Request Forgery reachable from a single, unprivileged wallet action (`dl_new_mirror`) with no signature/ownership requirement tying the URL to the store owner.

## Likelihood Explanation
High likelihood: `dl_new_mirror` is a normal, unauthenticated (from the store owner's perspective) wallet operation — it requires no special privilege beyond having XCH to pay the coin amount/fee, and it explicitly allows targeting a `launcher_id` that the caller does not own. Any node that subscribes to a shared/public DataLayer store (a normal, encouraged workflow) is automatically exposed once `periodically_manage_data()` runs and pulls the malicious mirror URL into subscriptions.

## Recommendation
- In `DataLayerWallet.create_new_mirror()` / `dl_new_mirror` RPC, restrict mirror creation to launcher ids the caller actually owns, or otherwise require store-owner authorization for URLs that will be auto-fetched by other users' nodes.
- In `download_data.py`/`data_layer.py`, validate mirror/plugin URLs before making outbound requests: enforce an allow-listed scheme (e.g., `http`/`https` only), resolve and reject requests to loopback, link-local (including `169.254.169.254`), private/internal ranges, and other disallowed destinations, similar to standard SSRF mitigations recommended for the Grafana OnCall fix.
- Consider making mirror-URL fetching opt-in/reviewable per subscription rather than automatic for any mirror discovered on-chain.

## Proof of Concept
1. Attacker runs `chia data add_mirror --id <victim_store_id> --url http://169.254.169.254/latest/meta-data/ -a 1` (or calls `dl_new_mirror` RPC directly) targeting a `store_id` they do not own — succeeds because ownership is never checked (`chia/data_layer/data_layer_wallet.py:687-703`, `chia/wallet/wallet_rpc_api.py:3255-3274`).
2. The mirror coin confirms on-chain with the malicious URL embedded in its memo.
3. A victim node that is subscribed to `victim_store_id` runs its normal `periodically_manage_data()` loop; `update_subscriptions_from_wallet()` pulls the new mirror URL into its subscription table (`chia/data_layer/data_layer.py:979-985`).
4. On the next sync attempt, `fetch_and_validate()`/`insert_from_delta_file()`/`download_file()` issues an outbound HTTP request from the victim's node to the attacker-controlled internal/metadata URL (`chia/data_layer/data_layer.py:642-694`, `chia/data_layer/download_data.py:110-145`), completing the SSRF.

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
