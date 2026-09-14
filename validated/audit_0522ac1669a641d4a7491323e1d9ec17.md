### Title
SSRF via unvalidated DataLayer mirror URLs fetched by `fetch_and_validate()`/`download_file()` - (File: chia/data_layer/download_data.py, chia/data_layer/data_layer.py)

### Summary
Chia's DataLayer sync loop fetches delta/full-tree files from server URLs that originate from on-chain "mirror" coins, which any wallet user can create for an arbitrary `store_id` by paying `dl_new_mirror` with attacker-chosen `urls`. These URLs are never validated (no scheme/host allow-list, no blocking of loopback/link-local/private ranges), and are handed directly to `aiohttp.ClientSession` in `http_download`/`download_file`, mirroring the exact bug class in the reported Open WebUI OAuth `_process_picture_url()` SSRF: user/attacker-controlled URL consumed by a server-side HTTP client without `validate_url()`-style filtering.

### Finding Description
Mirror coins are created via `dl_new_mirror` and can carry arbitrary URL strings; the puzzle only requires paying an amount tied to the store's `launcher_id` and does not authenticate the store owner or validate URL content [1](#0-0) . When a coin is added, `DataLayerWallet.coin_added()` decodes these attacker-supplied URLs straight from the spend and stores them as `Mirror` records without any validation [2](#0-1) .

`DataLayer.update_subscriptions_from_wallet()` pulls these mirror URLs from wallet RPC and inserts them as subscription server URLs for the store, again with no filtering beyond trailing-slash stripping [3](#0-2) .

The periodic sync loop `fetch_and_validate()` then randomly selects one of these server URLs and passes it into `insert_from_delta_file()` → `download_file()` → `http_download()`, issuing outbound HTTP requests via `aiohttp` to whatever URL was embedded in the mirror coin, with no SSRF guard (no `validate_url()`, no denylist of internal/loopback/link-local/metadata addresses) [4](#0-3) [5](#0-4) .

This is the same bug class as the reported issue: a server process makes outbound requests to a URL supplied through an untrusted, externally-influenced data path (OAuth `picture` claim there; on-chain mirror-coin URL here) without applying URL validation/SSRF protections, despite the codebase's own documentation explicitly acknowledging that "Plugin and mirror URLs are external trust inputs" [6](#0-5) .

### Impact Explanation
Any wallet user who can construct and push a `dl_new_mirror` transaction (an ordinary, unprivileged wallet action, not requiring ownership of the target store) can plant an arbitrary URL that a victim's DataLayer node will fetch when tracking/subscribing to that store id. This can be leveraged to:
- Probe/hit internal network services and localhost-bound endpoints reachable from the node's host.
- Target cloud metadata endpoints (e.g. `169.254.169.254`) if the DataLayer service runs in a cloud VM/container, potentially exposing metadata/credentials indirectly through timing/error behavior.
- Cause repeated outbound connections to attacker-controlled or third-party infrastructure, and induce excessive resource use / minor denial-of-service in the DL sync loop, since failures are retried with backoff rather than rejected outright.

Because DataLayer subscriptions are commonly used by DL clients querying public stores, this is reachable by any spend-bundle submitter who funds a mirror coin, matching the required reachability bar (single wallet action / unprivileged Data-Layer client interaction).

### Likelihood Explanation
Likelihood is High: creating a mirror coin with a malicious URL requires only a standard `dl_new_mirror` RPC call and a small fee/amount — no special privileges, no store ownership, and no consensus-level restriction on URL content. Any node that subscribes to (or owns and reconciles history for) that store will automatically and periodically attempt the fetch as part of its normal `periodically_manage_data()` loop.

### Recommendation
Apply the same fix pattern already suggested for the analogous Open WebUI issue: validate all mirror/subscription URLs before they are persisted or dereferenced.
- Validate URLs in `DataLayerWallet.coin_added()` before creating `Mirror` records, and/or in `DataLayer.update_subscriptions_from_wallet()`/`DataLayer.subscribe()` before writing subscription URLs.
- Enforce an allow-list of schemes (`http`/`https` only) and resolve+reject requests targeting loopback, link-local (including `169.254.169.254`), private (RFC1918), and other non-routable/internal address ranges before calling `aiohttp.ClientSession.get`/`post` in `http_download()` and `get_downloader()`.
- Consider re-validating on each fetch (DNS rebinding protection), not just at insertion time.

### Proof of Concept
1. Start a Chia full node + wallet + DataLayer service (`chia start data`).
2. From an unprivileged wallet, call `dl_new_mirror` targeting any known `launcher_id` (does not need to be owned) with `urls=["http://169.254.169.254/latest/meta-data/iam/security-credentials/"]` or `["http://127.0.0.1:<internal-port>/"]`, pay the required amount/fee, and push the transaction:
   ```
   await client.dl_new_mirror(DLNewMirror(launcher_id=target_launcher_id, amount=uint64(1000), urls=["http://169.254.169.254/latest/meta-data/"], fee=uint64(...), push=True), DEFAULT_TX_CONFIG)
   ```
3. Once confirmed, any DataLayer node tracking/subscribed to `target_launcher_id` (via `update_subscriptions_from_wallet` → `fetch_and_validate`) will select this mirror URL and issue an outbound HTTP GET/POST to it through `http_download`/`download_file`, confirming the SSRF request is made from the victim node without any URL validation.

### Citations

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

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
