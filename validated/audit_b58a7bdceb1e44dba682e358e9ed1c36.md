### Title
Unvalidated DataLayer mirror URLs enable SSRF against subscribing nodes - ([File: chia/data_layer/data_layer.py])

### Summary
DataLayer mirror coins allow any chain participant to publish arbitrary URLs on-chain for any store id, and other nodes that track/subscribe to that store id will later fetch data from those URLs during sync, with no validation that the destination is not an internal/private network address. This mirrors the FastGPT bug class: a "create" path (mirror creation) has no destination validation, and the stored value is later consumed by backend network fetch logic (`fetch_and_validate`/`download_file`) without revalidating the destination.

### Finding Description
Anyone can spend any coin they own to create a `create_mirror_puzzle()` output whose memo encodes an arbitrary `launcher_id` (any DataLayer store id) and an arbitrary list of URLs, via `create_new_mirror()` [1](#0-0) . This spend is permissionless — nothing ties the mirror memo's `launcher_id`/URL to ownership of that singleton.

When any other node is tracking that `launcher_id` locally (e.g. subscribed to the store), `DataLayerWallet.coin_added()` detects the mirror coin, decodes the URL memo, and persists it via `dl_store.add_mirror()` without validating the URL scheme or target host [2](#0-1) .

Later, `DataLayer.fetch_and_validate()` pulls candidate server URLs from `get_available_servers_for_store()`, shuffles them, and issues an HTTP request to whichever mirror URL is selected via `insert_from_delta_file()` / `download_file()` / `http_download()`, with no SSRF filtering of the destination (no scheme allowlist, no private/loopback IP block) [3](#0-2) [4](#0-3) . Tests explicitly demonstrate that loopback URLs like `http://127.0.0.1/8000` are accepted and stored as mirror URLs without rejection [5](#0-4) .

This is directly analogous to the FastGPT SSRF: the "create" boundary (mirror-coin creation / on-chain URL publication) does not validate the destination, and the value is later consumed by backend network code (DataLayer sync/fetch) without revalidation — exactly the "stored URL bypasses SSRF check at use time" pattern in the advisory.

### Impact Explanation
A low-privilege chain participant (any wallet holder who can spend one coin, no special permission on the target store) can cause any node that subscribes to or owns a targeted DataLayer store to make outbound HTTP requests to attacker-chosen internal/private endpoints (`http://127.0.0.1:...`, cloud metadata IPs, internal RPC ports, etc.). This can be used to probe or interact with internal services reachable from the victim's DataLayer host, a classic SSRF impact. Because DataLayer runs as a backend service tied to the wallet/full-node stack, this could expose internal RPC endpoints or other local-only services to remote-triggered requests.

### Likelihood Explanation
Likelihood is Medium: the attacker needs no elevated permission — they only need to spend a coin creating the mirror puzzle with a crafted memo, and the target node must be tracking/subscribed to the referenced store id (which is public information for any published DataLayer store, since store subscription is a normal use case). Farming a mirror coin costs mempool/on-chain fee, but there is no other barrier to publishing malicious URLs.

### Recommendation
Validate mirror URLs before persisting them and/or before making network requests: reject `localhost`/loopback, link-local, private (RFC1918), and other internal/reserved address ranges (resolve DNS and check resulting IPs, not just the literal string), similar to how MCP tool create/update should apply the same SSRF checks as the direct run endpoints in the referenced FastGPT patch. Apply this validation both at mirror ingestion (`DataLayerWallet.coin_added()` / `dl_store.add_mirror()`) and at fetch time (`download_file`/`http_download`) so a revalidation gap cannot be reintroduced by any future caller.

### Proof of Concept
1. Attacker publishes a mirror coin for an existing (target) DataLayer `store_id` with `urls=["http://127.0.0.1:PORT/..."]` or `["http://169.254.169.254/..."]` using `chia data add_mirror` / `dl_new_mirror` RPC — no ownership of the store is required, as shown by the create path [1](#0-0) .
2. Once confirmed on-chain, any node tracking that `store_id` ingests the mirror URL unvalidated [2](#0-1) .
3. During its next sync cycle, the victim node's `fetch_and_validate()` may select the malicious mirror and issue an HTTP GET to the attacker-chosen internal address [6](#0-5) , confirmed feasible by the existing unit test that stores `http://127.0.0.1/8000` as a valid mirror URL without any rejection [5](#0-4) .

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

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2782-2791)
```python
        urls = ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        res = await data_rpc_api.add_mirror({"id": store_id.hex(), "urls": urls, "amount": 1, "fee": 1})

        await farm_block_check_singleton(data_layer, full_node_api, ph, store_id, wallet=wallet_rpc_api.service)
        mirrors = await data_rpc_api.get_mirrors({"id": store_id.hex()})
        mirror_list = mirrors["mirrors"]
        assert len(mirror_list) == 1
        mirror = mirror_list[0]
        assert mirror["urls"] == ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        coin_id = mirror["coin_id"]
```
