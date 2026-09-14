### Title
DataLayer Mirror URLs Enable Server-Side Request Forgery (SSRF) via Unvalidated On-Chain Mirror URLs - (File: `chia/data_layer/download_data.py`)

### Summary
Any wallet user can publish an on-chain "mirror" coin for **any** DataLayer store (not just their own) containing arbitrary attacker-controlled URL strings. Every other node that tracks/subscribes to that store automatically ingests these URLs into its local subscription list and periodically issues outbound HTTP GET requests to them with no scheme, hostname, or private-network validation — a direct SSRF analog to the MLflow `_create_webhook()`/`_send_webhook_request()` bug.

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a standard coin spend whose memos embed `launcher_id` and a list of caller-supplied `urls`, with no check that the caller owns or is otherwise authorized for that `launcher_id`: [1](#0-0) 

The wallet RPC endpoint that exposes this to any wallet client performs no additional validation either: [2](#0-1) 

When any node that already tracks that `launcher_id` observes the mirror coin on-chain, it unconditionally records the attacker-supplied URLs into its wallet DB: [3](#0-2) 

The DataLayer service then pulls these URLs straight from wallet RPC into its local subscription/server list with no sanitization (no scheme allowlist, no private/loopback/metadata-address filtering): [4](#0-3) 

During the periodic sync loop, `fetch_and_validate()` picks one of these attacker-controlled `ServerInfo.url` values and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which performs an `aiohttp` GET directly against `server_info.url + "/" + filename`: [5](#0-4) [6](#0-5) 

No part of this pipeline validates that the URL is `http`/`https`-only pointed at an expected external mirror, nor blocks internal/loopback/link-local/cloud-metadata addresses (e.g. `http://169.254.169.254/...`, `http://127.0.0.1:PORT/...`, or internal service hostnames). The project's own DataLayer notes explicitly flag mirror URLs as untrusted external input, confirming this is a recognized-but-unmitigated trust boundary: [7](#0-6) , [8](#0-7) 

### Impact Explanation
Any user who can (a) subscribe/track a DataLayer store and (b) submit an ordinary spend bundle creating a mirror coin can force every other DL node tracking that store to make outbound HTTP requests to an arbitrary attacker-chosen URL/host/port on a schedule (the periodic `update_subscription()` loop). This can be used to:
- Probe/attack internal network services reachable from the victim's node (SSRF pivot).
- Query cloud metadata endpoints on cloud-hosted DL nodes, potentially exfiltrating instance credentials via response side effects (e.g. failure/backoff timing, or if the response body incidentally gets logged).
- Trigger repeated outbound connections to attacker infrastructure, enabling network reconnaissance or resource exhaustion against a third party via the victim node acting as a proxy.

This matches the "coin-set/consistency-observable node divergence" or "spend-triggered processing" analog class insofar as it is a concrete, spend-triggered, unauthenticated (from the victim's perspective) forced network action with no cost paid by the victim.

### Likelihood Explanation
High likelihood: creating a mirror coin is a normal, cheap, permissionless DataLayer wallet operation (`dl_new_mirror`/`add_mirror`), requiring only that some victim node already tracks the targeted `launcher_id` — which is the standard/expected state for any publicly subscribed DataLayer store. No special role, admin RPC access, or race condition is needed.

### Recommendation
- Enforce launcher-id ownership (or an explicit allow-list/consent step) before accepting mirror URLs for automatic subscription use, or clearly separate "advisory" mirror URLs from "trusted, auto-fetched" ones.
- Validate mirror/plugin URLs before use in `update_subscriptions_from_wallet()` and `http_download()`: restrict scheme to `http`/`https`, resolve and reject requests to loopback, link-local (including `169.254.169.254`), private, and other non-routable address ranges, and consider requiring operator opt-in/allowlisting of mirror hosts.
- Apply the same validation to plugin `downloader`/`uploader` URLs (`get_downloader()`, `get_uploaders()`), since these also perform unauthenticated outbound POSTs to configured or discovered URLs.

### Proof of Concept
1. Attacker runs a normal `chia` wallet with DataLayer enabled and tracks a public store `store_id = launcher_id_X` (via `dl_track_new`), which many other honest DL nodes also track.
2. Attacker calls `chia rpc wallet dl_new_mirror` (or the CLI `add_mirror` command) with `launcher_id_X` and `urls=["http://169.254.169.254/latest/meta-data/iam/security-credentials/"]`, paying only the standard mirror coin amount/fee — see `chia/cmds/data.py:474-511` and `DataLayerRpcApi.add_mirror` at `chia/data_layer/data_layer_rpc_api.py:468-475`.
3. Once the mirror coin confirms on-chain, every other node tracking `launcher_id_X` picks it up via `DataLayerWallet.coin_added()` (`chia/data_layer/data_layer_wallet.py:775-800`) and stores the malicious URL as a `Mirror`.
4. Those nodes' `DataLayer.update_subscriptions_from_wallet()` (`chia/data_layer/data_layer.py:979-985`) copies the URL into `ServerInfo`, and the next `fetch_and_validate()`/`http_download()` cycle (`chia/data_layer/data_layer.py:642-694`, `chia/data_layer/download_data.py:298-324`) issues an outbound GET from the victim node directly to the attacker-chosen internal/metadata URL.

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

**File:** chia/data_layer/download_data.py (L298-324)
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
            log.debug(f"Downloading delta file {filename}. Size {size} bytes.")
```

**File:** .cursor/context/data-layer.md (L26-28)
```markdown
- Do not treat a local root as current chain truth until wallet confirmation status has been reconciled.
- Do not treat mirror URLs, plugins, or static file names as trusted data sources.
- Do not collapse `None`, omitted root fields, and empty-root sentinels across RPC/service/wallet boundaries.
```

**File:** .cursor/context/data-layer.md (L87-88)
```markdown
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
