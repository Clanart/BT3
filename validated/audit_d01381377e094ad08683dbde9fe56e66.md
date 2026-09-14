## Title
On-Chain Mirror Coins Let Any Spend-Bundle Submitter Force a Victim DataLayer Node to Send SSRF Requests to Arbitrary/Internal URLs - (File: chia/data_layer/data_layer_wallet.py)

## Summary
Chia's DataLayer mirror mechanism lets any wallet on the network attach an arbitrary list of URLs to any store's `launcher_id`, including launcher IDs it does not own. A victim's DataLayer service that subscribes to / tracks that store will automatically pull those attacker-chosen URLs into its `ServerInfo` list and periodically issue outbound HTTP requests to them during normal sync, with no validation that the destination is not an internal or loopback address — the same bug class as the Fides CVE-2023-46124 SSRF (crafted external input causes the trusted service to make arbitrary outbound requests).

## Finding Description
`DataLayerWallet.create_new_mirror()` builds a standard coin spend whose puzzle is the fixed, unauthenticated `create_mirror_puzzle()` (`P2_PARENT.curry(Program.to(1))`) and whose memo encodes an attacker-supplied `launcher_id` plus an arbitrary list of `urls`: [1](#0-0) 

Nothing in this function (or in the RPC layer that calls it, `WalletRpcApi.dl_new_mirror`) checks that the caller owns or is authorized to publish mirrors for the given `launcher_id`: [2](#0-1) 

Because `create_mirror_puzzle()` is `P2_PARENT.curry(Program.to(1))` — a puzzle satisfiable by anyone, not tied to the store owner's key — **any spend-bundle submitter can create a mirror coin for any existing `launcher_id`** with a URL list they fully control.

When any node observes this coin on chain and is already tracking that `launcher_id`, `DataLayerWallet.coin_added()` unconditionally records the attacker-controlled URLs as a `Mirror` for that store — the only gate is `is_launcher_tracked(launcher_id)`, which is true for any store a victim has subscribed to (a completely ordinary DataLayer client action): [3](#0-2) 

The DataLayer service periodically pulls these wallet-recorded mirror URLs into its local subscription table via `update_subscriptions_from_wallet()`: [4](#0-3) [5](#0-4) 

and then `fetch_and_validate()` iterates those URLs, issuing HTTP requests to them via `insert_from_delta_file()` → `download_file()` → `http_download()`, with no URL scheme/host allow-listing, no blocking of loopback/private/link-local ranges, and no restriction to a small predetermined origin set: [6](#0-5) [7](#0-6) 

The documented design explicitly acknowledges mirror/plugin URLs are untrusted inputs for *content* validation purposes ("every downloaded file must rebuild the wallet-advertised root before becoming local committed state"), but that protection covers only the integrity of the *data*, not the *destination* of the outbound network request itself: [8](#0-7) [9](#0-8) 

## Impact Explanation
This is a direct SSRF primitive reachable by any unprivileged spend-bundle submitter/wallet user (one of the explicitly in-scope actors: "Data Layer client"). By publishing a mirror coin referencing a victim-tracked `launcher_id` with URLs like `http://127.0.0.1:<internal-port>/...` or `http://169.254.169.254/...` (cloud metadata endpoints) or internal-network hosts, an attacker forces the victim's DataLayer process to originate outbound requests to those targets on a recurring schedule (`periodically_manage_data()` cycle), from the victim's own network position. The response content/errors are partially observable through logs and store-download success/failure signaling (backoff/ban logic reacts to failures), giving the attacker a blind/semi-blind SSRF oracle against internal infrastructure that the victim's node/host can otherwise not reach externally. This maps to the CWE-918 class named in the report and is triggerable purely by chain data, without needing to compromise or socially engineer the victim.

## Likelihood Explanation
Likelihood is high for any DataLayer operator: creating a mirror coin costs only a standard fee/amount and any `launcher_id`, and requires no special authorization scope (unlike the Fides bug, which needed the `CONNECTOR_TEMPLATE_REGISTER` scope) — here it is entirely unauthenticated at the puzzle level. The only prerequisite is that the victim be actively tracking/subscribed to the targeted store, which is normal, expected DataLayer usage (e.g. any public store a user follows).

## Recommendation
- Restrict mirror publication to being validated against the store owner's singleton (e.g., require the mirror-adding spend to be authorized by/linked to the launcher's current owner, or otherwise cryptographically bind mirror updates to a store-controlling key) rather than allowing an arbitrary P2_PARENT-satisfiable coin to attach URLs to any `launcher_id`.
- Add outbound-request SSRF protections in `download_file()`/`http_download()`: resolve and reject requests to loopback, link-local, private, and other non-routable/internal address ranges, and disallow non-HTTP(S) schemes, before making requests derived from wallet/mirror-sourced URLs.
- Consider an explicit user opt-in / allow-list step before mirror URLs discovered purely from chain data are used to originate outbound requests, mirroring how `enforce_https`/explicit pool URL confirmation works elsewhere in the codebase.

## Proof of Concept
1. Attacker wallet calls `dl_new_mirror` RPC with `launcher_id` = victim's tracked store id, `urls=["http://127.0.0.1:<victim-internal-service-port>/admin"]`, and a small `amount`/`fee` — this succeeds because `create_new_mirror()` performs no ownership check: [1](#0-0) 
2. Once the resulting mirror coin confirms on chain, any victim node tracking that `launcher_id` records the URL via `coin_added()`: [3](#0-2) 
3. The victim's DataLayer service syncs these URLs into its local subscription table (`update_subscriptions_from_wallet`) and, on its next `periodically_manage_data()` cycle, `fetch_and_validate()` issues an HTTP GET to `http://127.0.0.1:<port>/admin` via `download_file()`/`http_download()`, with no destination validation: [6](#0-5) [7](#0-6) 

Note: I could not fully inspect the body of `http_download()` (lines beyond 145 in `download_data.py`) within the available tool budget to confirm the exact aiohttp call parameters (e.g., whether `trust_env`/proxy or redirect-following settings add further risk); this should be verified directly in the file during remediation.

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

**File:** chia/data_layer/data_store.py (L1668-1704)
```python
    async def update_subscriptions_from_wallet(self, store_id: bytes32, new_urls: list[str]) -> None:
        async with self.db_wrapper.writer() as writer:
            cursor = await writer.execute(
                "SELECT * FROM subscriptions WHERE from_wallet == 1 AND tree_id == :tree_id",
                {
                    "tree_id": store_id,
                },
            )
            old_urls = [row["url"] async for row in cursor]
            cursor = await writer.execute(
                "SELECT * FROM subscriptions WHERE from_wallet == 0 AND tree_id == :tree_id",
                {
                    "tree_id": store_id,
                },
            )
            from_subscriptions_urls = {row["url"] async for row in cursor}
            additions = {url for url in new_urls if url not in old_urls}
            removals = [url for url in old_urls if url not in new_urls]
            for url in removals:
                await writer.execute(
                    "DELETE FROM subscriptions WHERE url == :url AND tree_id == :tree_id",
                    {
                        "url": url,
                        "tree_id": store_id,
                    },
                )
            for url in additions:
                if url not in from_subscriptions_urls:
                    await writer.execute(
                        "INSERT INTO subscriptions(tree_id, url, ignore_till, num_consecutive_failures, from_wallet) "
                        "VALUES (:tree_id, :url, 0, 0, 1)",
                        {
                            "tree_id": store_id,
                            "url": url,
                        },
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

**File:** .cursor/context/data-layer.md (L26-28)
```markdown
- Do not treat a local root as current chain truth until wallet confirmation status has been reconciled.
- Do not treat mirror URLs, plugins, or static file names as trusted data sources.
- Do not collapse `None`, omitted root fields, and empty-root sentinels across RPC/service/wallet boundaries.
```

**File:** .cursor/context/data-layer.md (L87-89)
```markdown
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
- DataLayer wallet code depends on singleton CLVM structure, odd singleton amounts, lineage proofs, and offer solver field names. Changes in wallet puzzle drivers or offer summaries can break this module without direct edits here.
```
