## Finding

### Title
Unauthenticated SSRF via attacker-controlled DataLayer mirror URLs fetched without validation - (File: `chia/data_layer/download_data.py`)

### Summary
Chia's DataLayer subsystem lets any wallet holder publish "mirror" server URLs on-chain by spending a coin whose memos encode arbitrary strings. These URLs are later read back by every node that subscribes to (or owns/tracks) that store and are used directly as HTTP request targets to fetch delta/full-tree files, with no validation of scheme, host, or destination (no blocking of loopback/link-local/cloud-metadata addresses, no redirect filtering).

### Finding Description
`DataLayerWallet.create_new_mirror()` creates a mirror coin whose memo field stores an arbitrary list of attacker-supplied URL strings with zero validation: [1](#0-0) 

When any node observes this coin being created (via normal wallet sync, e.g. cross-network mirror tracking or a node that has independently tracked the same launcher id), `DataLayerWallet.coin_added()` decodes the URL list straight out of the spend's puzzle reveal/solution and persists it as a `Mirror` record with no validation of the URL content: [2](#0-1) 

`DataLayer.update_subscriptions_from_wallet()` then pulls these mirror URLs (via wallet RPC `dl_get_mirrors`) into the local subscription's server list: [3](#0-2) 

During the periodic sync loop, `DataLayer.fetch_and_validate()` iterates over these `server_info` entries and calls `insert_from_delta_file()`/`download_file()` with the raw attacker-controlled `url`: [4](#0-3) 

Finally, `http_download()` performs the actual outbound network request straight to `server_info.url + "/" + filename` using `aiohttp.ClientSession.get`, with no scheme allow-list, no DNS/IP validation, and no restriction against internal or cloud metadata addresses: [5](#0-4) 

The plugin-mediated upload/download path (`get_uploaders`, `upload_files`) has the same shape but is bounded to locally-configured plugin URLs, so the primary unauthenticated attack surface is the direct HTTP downloader path fed by on-chain mirror data.

### Impact Explanation
Any coin holder (an "unprivileged spend-bundle submitter"/DataLayer client) can create a mirror coin with a URL such as `http://169.254.169.254/latest/meta-data/` or `http://<internal-host>:<port>/admin`. Any full node/DataLayer service that later tracks or subscribes to that store id (this happens automatically for cross-network singleton tracking and any peer that chooses to subscribe to a public store) will issue outbound HTTP GET requests to that URL as part of its normal sync loop, without the operator's knowledge or consent. This can be used to probe/exploit internal services, hit cloud provider metadata endpoints to exfiltrate credentials, or perform request smuggling/floods against internal infrastructure that the DataLayer host can reach.

### Likelihood Explanation
Publishing a mirror with attacker-controlled URLs only requires a coin spend and small fee — no special privilege is required, and there is no validation anywhere in the pipeline that filters scheme, host, or IP ranges before the URL is stored or fetched. Any node running the DataLayer service and subscribing to (or owning-and-cross-tracking) the targeted store id will trigger the SSRF automatically during its periodic `update_subscription()`/`fetch_and_validate()` cycle.

### Recommendation
Validate mirror URLs both at creation time (`create_new_mirror`) and at consumption time (`fetch_and_validate`/`http_download`): enforce an `http(s)` scheme allow-list, resolve and reject requests targeting loopback, link-local (including `169.254.169.254`), private, and other non-routable/internal address ranges, and disable/limit automatic redirect following in the aiohttp client used for mirror downloads.

### Proof of Concept
1. Attacker calls `dl_new_mirror` (via wallet RPC or `chia data add_mirror`) on a store id, supplying `urls=["http://169.254.169.254/latest/meta-data/iam/security-credentials/"]` and a small `amount`/`fee`.
2. Once the spend confirms, `DataLayerWallet.coin_added()` persists the mirror with this URL for any node tracking that launcher id.
3. A victim node that subscribes to (or tracks) that store id runs `DataLayer.periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()` → `download_file()` → `http_download()`, which issues an outbound `GET` to the attacker-chosen URL, demonstrating SSRF against internal/cloud-metadata endpoints reachable from the victim host. [6](#0-5)

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

**File:** chia/data_layer/data_layer.py (L60-65)
```python
from chia.server.server import ChiaServer
from chia.server.ws_connection import WSChiaConnection
from chia.util.async_pool import Job, QueuedAsyncPool
from chia.util.path import path_from_root
from chia.util.task_referencer import create_referenced_task
from chia.wallet.puzzle_drivers import Solver
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
