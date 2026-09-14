Confirmed — no URL validation exists anywhere in the DataLayer mirror/subscription/download path. This confirms a genuine SSRF analog to the soft-serve webhook vulnerability.

### Title
DataLayer Mirror URLs Enable SSRF via Unvalidated Outbound HTTP Fetch - (File: `chia/data_layer/download_data.py`)

### Summary
Chia's Data Layer feature lets any wallet holder publish "mirror" URLs on-chain for **any** DataLayer store (not just their own), by spending their own coins to a well-known, un-permissioned puzzle. Any full node that owns or subscribes to that store's launcher id will later pick up those attacker-supplied URLs and issue outbound `aiohttp` HTTP GET/POST requests to them — with no scheme/host/IP validation — mirroring the exact SSRF pattern reported for Soft Serve's webhook delivery (unvalidated user-controlled URL → automatic outbound server-side request).

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a standard-wallet spend that creates a coin with the puzzle hash of the fixed, unparameterized `create_mirror_puzzle()` (`P2_PARENT.curry(Program.to(1))`) and encodes an arbitrary `launcher_id` plus arbitrary URL strings into the coin's memos. Crucially, there is no check that the caller owns or controls `launcher_id`: [1](#0-0) 

Any peer that broadcasts this transaction can therefore mint a "mirror" coin advertising arbitrary URLs for a store id belonging to someone else.

When such a coin is seen by any wallet tracking that launcher id, `DataLayerWallet.coin_added()` decodes the memos and unconditionally stores the URLs as a mirror for that launcher id — it only checks that the launcher id is tracked, not who created the coin: [2](#0-1) 

`DataLayer.update_subscriptions_from_wallet()` then pulls these mirror URLs directly from wallet RPC and inserts them into the local subscription table as candidate download servers, with no validation: [3](#0-2) 

The background sync loop (`fetch_and_validate()`) subsequently picks a server URL from this table and passes it straight into `insert_from_delta_file()` → `download_file()` → `http_download()`, which performs an unauthenticated `aiohttp` GET to `server_info.url + "/" + filename` with no host/IP allow-listing (no check against localhost, RFC1918 ranges, or the cloud metadata address `169.254.169.254`): [4](#0-3) [5](#0-4) 

The internal architecture notes explicitly acknowledge mirror URLs are untrusted input but do not indicate any mitigation was added: [6](#0-5)  and [7](#0-6) .

### Impact Explanation
Any wallet user — not the store owner, not an operator, and requiring only enough mojos to fund a mirror coin — can cause any Data Layer node that tracks (owns or subscribes to) the targeted store to make outbound HTTP requests to attacker-chosen destinations:
- Cloud metadata theft (`http://169.254.169.254/...`) from nodes running in cloud VMs.
- Internal network / localhost service probing and port scanning via response codes and timing (`ClientConnectorError` vs successful response is distinguishable and logged, and repeated failures trigger a visible ban/backoff sequence in `server_misses_file()`).
- Because this abuses the on-chain, permissionless mirror-publishing mechanism, the attack is reachable purely from a submitted spend bundle/wallet action, matching the required threat model (spend-bundle submitter / Data Layer client), with no compromised peer or operator access needed.

### Likelihood Explanation
High. Creating a mirror coin only requires a small `amount` of mojos, is a standard, well-documented DataLayer wallet operation (`add_mirror` CLI/RPC), and does not require any relationship to the target store beyond knowing its `launcher_id` (which is public, on-chain data). Any Data Layer node that tracks the same store (as owner or subscriber) will pick up the URLs during its normal periodic subscription sync.

### Recommendation
Validate and restrict mirror/subscription URLs before they are used for outbound HTTP requests in `download_data.py`/`data_layer.py`: reject non-HTTP(S) schemes, resolve and block loopback/link-local/private/multicast/metadata IP ranges (including DNS-rebinding protection by re-checking the resolved IP at connect time), and consider requiring explicit operator opt-in/allow-listing for mirror URLs originating from unowned stores rather than trusting on-chain mirror data implicitly.

### Proof of Concept
1. Attacker wallet calls `create_new_mirror` (e.g. via `dl_new_mirror` RPC / `chia data add_mirror`) with `launcher_id` set to a **victim's** DataLayer store id and `urls=["http://169.254.169.254/latest/meta-data/iam/security-credentials/", "http://127.0.0.1:2379/v2/keys"]`, funded from the attacker's own coins.
2. Broadcast the resulting spend bundle; once farmed, the mirror coin is confirmed on-chain.
3. Any node (including the victim's own Data Layer node) that tracks the victim's `launcher_id` observes the coin via `coin_added()`, stores the URLs as a mirror, and `update_subscriptions_from_wallet()` propagates them into `DataStore` subscriptions.
4. During the next `periodically_manage_data()` cycle, `fetch_and_validate()` → `download_file()` → `http_download()` issues an outbound GET to the attacker-controlled/internal URL, exposing whether the internal service/metadata endpoint is reachable and returning response data into node logs/state.

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

**File:** .cursor/context/data-layer.md (L26-27)
```markdown
- Do not treat a local root as current chain truth until wallet confirmation status has been reconciled.
- Do not treat mirror URLs, plugins, or static file names as trusted data sources.
```

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
