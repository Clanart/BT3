### Title
Unvalidated Mirror URLs Enable Server-Side Request Forgery via DataLayer Sync - ([File: chia/data_layer/download_data.py])

### Summary
Any wallet holder can publish a "mirror" URL for **any** DataLayer store id, including stores they don't own, by spending a coin to the mirror puzzle with an arbitrary URL in the memo. Every peer that tracks that store id will ingest the URL as a trusted download server and later have its DataLayer service issue outbound HTTP requests to it with no validation of scheme, host, or destination — an SSRF primitive analogous to the cBioPortal `/proxy` SSRF (CVE‑2024‑41668), where an unauthenticated caller could make the server issue attacker-controlled outbound requests.

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet build a transaction that pays into `create_mirror_puzzle()` with a memo `[launcher_id, *urls]` — the `launcher_id` and `urls` are fully attacker-chosen and are not required to correspond to a store the spender owns: [1](#0-0) 

When any node sees this coin on chain, `DataLayerWallet.coin_added()` decodes the memo and stores the mirror **as long as the referenced `launcher_id` is tracked locally** — there is no check that the mirror-creating coin is spent by the store owner, and no validation of the URL contents: [2](#0-1) 

The service layer then pulls these attacker-controlled mirror URLs into its subscription/server list unconditionally: [3](#0-2) 

During periodic sync, `fetch_and_validate()` picks a server from this list and calls `insert_from_delta_file()` / `download_file()`, which — when no plugin downloader is configured — calls `http_download()`: [4](#0-3) [5](#0-4) 

`http_download()` performs an unauthenticated `aiohttp` GET directly against the attacker-supplied URL with no allow-list, no restriction on private/link-local addresses, and only filename-shape validation applied afterward on the response body, not on the target itself: [6](#0-5) 

This mirrors the cBioPortal bug class: a component reachable by an unprivileged/remote actor accepts an attacker-controlled URL and has the server perform the outbound fetch, enabling SSRF against internal services (localhost admin panels, cloud metadata endpoints, internal RPC ports) reachable from the victim node's network context.

### Impact Explanation
Any coin holder can force every peer that tracks a given DataLayer store to make outbound HTTP requests to attacker-chosen targets (e.g. `http://127.0.0.1:<internal-port>/...` or cloud metadata IPs) whenever that node syncs the store. This is a network-reachable SSRF affecting DataLayer clients/subscribers, potentially exposing internal services, enabling internal network reconnaissance/pivoting, or interacting with local RPC/admin interfaces that are normally only reachable from localhost. This aligns with a High-severity SSRF classification consistent with the cBioPortal advisory (CVSS 8.3).

### Likelihood Explanation
Likelihood is high for anyone running the DataLayer service: creating a mirror coin only requires standard wallet spend capability and a small fee/amount; no ownership of the target store or elevated privileges is required, and mirror ingestion/sync happens automatically in the background sync loop (`periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()`) for any tracked store.

### Recommendation
- Restrict mirror ingestion to URLs that pass a strict allow-list/validation (scheme restricted to `http(s)`, reject private/loopback/link-local/metadata IP ranges) before persisting them via `DataLayerStore.add_mirror()`.
- Consider requiring mirror-coin authorization tied to the store owner (e.g., verify the spender/owner relationship) rather than accepting any coin matching the mirror puzzle hash for a tracked launcher id.
- Apply the same URL validation in `http_download()` / `download_file()` immediately before making the outbound request, independent of where the URL originated (mirror, plugin, or config), and make DNS resolution SSRF-safe (block redirects/resolution to internal ranges).

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` RPC (or `create_new_mirror`) targeting a `launcher_id` of a store that a victim node tracks, with `urls=["http://127.0.0.1:<victim-local-service-port>"]`, and gets the transaction confirmed. [7](#0-6) 
2. The victim's `DataLayerWallet.coin_added()` observes the mirror coin, confirms `is_launcher_tracked(launcher_id)` is true, and stores the attacker URL as a mirror. [8](#0-7) 
3. Victim's `DataLayer.update_subscriptions_from_wallet()` syncs this URL into its local subscription server list. [3](#0-2) 
4. On the next sync cycle, `fetch_and_validate()` → `insert_from_delta_file()` → `download_file()` → `http_download()` issues an outbound GET from the victim node to `http://127.0.0.1:<port>/<delta-filename>`, demonstrating SSRF against the victim's own internal network. [9](#0-8)

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

**File:** chia/data_layer/data_layer.py (L668-694)
```python
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

**File:** chia/data_layer/download_data.py (L131-145)
```python
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

**File:** chia/data_layer/download_data.py (L298-323)
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
