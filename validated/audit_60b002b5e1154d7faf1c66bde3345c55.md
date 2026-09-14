## Finding

### Title
DataLayer mirror URLs from unauthenticated on-chain coins are fetched via unrestricted outbound HTTP requests, enabling SSRF - (File: chia/data_layer/download_data.py)

### Summary
Any XCH holder can create a "mirror" coin that attaches an arbitrary URL to *any* DataLayer store's `launcher_id`—including stores they do not own. Any node that tracks/subscribes to that `launcher_id` will unconditionally add the attacker-supplied URL as a download server and later issue an outbound, unauthenticated HTTP GET to it. There is no validation that the URL/host is not private, loopback, or link-local (e.g. `127.0.0.1`, `169.254.169.254`), which is the exact bug class described in the `private-ip` SSRF advisory (missing/insufficient filtering of internal-network destinations before an outbound request is made).

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet create a coin with the fixed `create_mirror_puzzle()` puzzle hash and a memo of `[launcher_id, *urls]` [1](#0-0) . Nothing ties `launcher_id` to a store the spender owns—it is attacker-chosen data in a memo.

When that coin confirms, `DataLayerWallet.coin_added()` decodes the memo and, as long as the node is tracking that `launcher_id` at all (`is_launcher_tracked`), stores the mirror regardless of whether the mirror was created by the node's own wallet (`ours` is just a display flag, not a trust gate): [2](#0-1) 

`DataLayer.update_subscriptions_from_wallet()` then pulls *all* mirrors for the store (not filtered by `ours`) and feeds their URLs into the local subscription/server list used for downloads: [3](#0-2) 

During the periodic sync loop, `DataLayer.fetch_and_validate()` randomly picks one of these servers and downloads delta files from it via `insert_from_delta_file()` → `download_file()` → `http_download()`: [4](#0-3) [5](#0-4) 

`http_download()` performs `session.get(server_info.url + "/" + filename, ...)` with no scheme/host allowlist and no check against `chia.util.network.is_in_network`/`is_localhost`/private-range logic that exists elsewhere in the codebase (e.g. `chia/util/network.py`'s `is_in_network`, `is_trusted_cidr`, `is_localhost`) — those helpers are used for peer admission but are never applied to DataLayer mirror/server URLs. This is exactly the "private-ip" bug class: an SSRF-relevant destination check exists in the codebase for one subsystem but is absent for the DataLayer HTTP fetch path, so an attacker-controlled host string reaches `aiohttp` unfiltered.

### Impact Explanation
Any unprivileged spend-bundle submitter can force other Chia nodes that track a given DataLayer store (offer counterparties, DataLayer subscribers, or the store's own mirrors list) to issue outbound HTTP requests to attacker-chosen hosts, including RFC1918/loopback/link-local/cloud-metadata addresses. This can be used to probe or interact with internal-only services (local RPC ports, internal admin panels, cloud metadata endpoints) that trust requests originating from localhost/internal network, and to fingerprint internal network topology of node operators. This is a High-severity SSRF per the same CWE-918 classification as the referenced advisory, since it is remotely triggerable by any unprivileged party at the cost of a small on-chain fee.

### Likelihood Explanation
Likelihood is high: creating a mirror coin only requires a standard signed transaction (`generate_signed_transaction`) with a small `amount`/`fee`, no special permissions, and no ownership of the target `launcher_id`. Any node that is already tracking/subscribed to that store (a normal DataLayer operation) will automatically ingest the URL and periodically attempt to fetch from it as part of `periodically_manage_data()`'s regular sync loop, requiring no additional victim action beyond normal DataLayer usage.

### Recommendation
Before using any DataLayer mirror/server URL for an outbound fetch (`http_download`, plugin `download_file` POST), resolve and validate the host against private/loopback/link-local/multicast ranges (reusing/extending `chia.util.network.is_in_network`/`is_trusted_cidr` style checks) and reject or require explicit operator opt-in for such destinations, similar to the existing `enforce_https` check used for farmer pool URLs (`chia/farmer/farmer.py`). Additionally, consider only trusting mirrors flagged `ours=True` or mirrors explicitly allow-listed by the store owner/operator when auto-populating the subscription's server list, rather than trusting mirrors published by arbitrary third parties.

### Proof of Concept
1. Attacker crafts and broadcasts a standard spend that creates a coin with puzzle hash `create_mirror_puzzle().get_tree_hash()` and memo `[victim_tracked_launcher_id, b"http://169.254.169.254/latest/meta-data/iam/security-credentials/"]` (mirroring `DataLayerWallet.create_new_mirror`).
2. Once confirmed, any node tracking `victim_tracked_launcher_id` (e.g. a DataLayer subscriber or the store owner's own node) ingests this mirror via `coin_added()` and includes its URL in `update_subscriptions_from_wallet()`.
3. On the next `periodically_manage_data()`/`fetch_and_validate()` cycle, the victim node's `download_file()`/`http_download()` issues `GET http://169.254.169.254/latest/meta-data/.../<delta-filename>` with no destination filtering, completing the SSRF request to the internal/metadata endpoint.

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
