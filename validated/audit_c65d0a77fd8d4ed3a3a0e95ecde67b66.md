### Title
Server-Side Request Forgery via unrestricted mirror URL in Chia Data Layer file sync - (File: `chia/data_layer/download_data.py`)

### Summary
The GitLab advisory describes an SSRF where an attacker-controlled reference (a crafted tag rendered by the Jupyter Notebook viewer) causes the server to issue arbitrary outbound HTTP requests. In the Chia Data Layer subsystem, an analogous pattern exists: any wallet user can register a "mirror" URL for a Data Layer store via `DataLayerWallet.create_new_mirror` [1](#0-0) , and that arbitrary attacker-supplied URL string is later fetched over plain HTTP by any other node's `DataLayer` service without host/scheme restriction, via `http_download` in `download_data.py`.

### Finding Description
`create_new_mirror` lets any wallet holder create an on-chain "mirror" coin whose memo encodes a `launcher_id` and an arbitrary list of `urls` (raw bytes, attacker-controlled strings) [1](#0-0) . When this coin is seen by other wallets, `coin_added` decodes the URLs with no validation (no scheme allow-list, no host filtering) and stores them via `dl_store.add_mirror` [2](#0-1) .

Later, when a Data Layer node wants to sync a store it is subscribed to, `DataLayer.fetch_and_validate` pulls `servers_info` for the store (which originate from these attacker-registered mirror URLs) and, for each server, calls `insert_from_delta_file`, ultimately invoking `download_file`/`http_download` [3](#0-2) . `http_download` performs an unrestricted `aiohttp` GET request against `server_info.url + "/" + filename` with no validation that the URL points to a legitimate, external, non-private address [4](#0-3) .

This is directly analogous to the GitLab bug class (BIT-gitlab-2022-2428 / CVE-2022-2428): an untrusted, low-privilege actor supplies a URL/reference that is embedded in metadata (Jupyter notebook tag in GitLab; on-chain mirror-coin memo in Chia) and is later dereferenced by the server/node performing an unauthenticated outbound fetch, without restricting target host or scheme (e.g. `http://127.0.0.1:...`, cloud metadata endpoints, or internal RPC/services reachable from the machine running the Data Layer node).

### Impact Explanation
Any Data Layer node that subscribes to a store mirrored by a malicious peer will issue outbound HTTP requests to a URL fully controlled by that peer. This is a spend-triggered SSRF: a single confirmed spend bundle (the mirror-coin creation) is enough to make every subscriber node hit an arbitrary attacker-chosen internal endpoint (e.g., localhost RPC ports, cloud metadata services, other internal hosts on the operator's network) during subsequent generation-sync attempts by `fetch_and_validate`. Depending on deployment (colocated full node/wallet RPC, cloud VM metadata service, internal admin interfaces), this could disclose sensitive information or trigger unintended side effects on internal-only services reachable from the node's network position. It does not directly forge coin identity or move funds, but it is a concrete unauthorized network action triggered purely by an unprivileged, no-cost-to-attacker Data Layer state update.

### Likelihood Explanation
Likelihood is moderate-to-high for any operator running a Data Layer node subscribed to third-party (non-owned) stores: mirror creation only requires a minimal on-chain spend (`create_new_mirror`) and no signature or permission checks are performed on the URL content before it is persisted as a candidate server and later dereferenced by `fetch_and_validate`/`http_download`. The main mitigating factor is that exploitation requires the target operator's node to actually be subscribed to and syncing the attacker's store, which requires some interaction (subscribing to a store id) — this is a normal, encouraged Data Layer workflow, so it is readily reachable.

### Recommendation
Validate and restrict mirror URLs before persisting/using them: enforce an `https://`/`http://` scheme allow-list, resolve and reject requests to loopback, link-local, private, and cloud-metadata IP ranges (SSRF-safe resolver / block-list) before calling `http_download`, and consider requiring operator confirmation before adding untrusted mirrors as active download sources in `chia/data_layer/download_data.py` and `chia/data_layer/data_layer.py`.

### Proof of Concept
1. Attacker wallet calls `create_new_mirror(launcher_id, amount, urls=[b"http://169.254.169.254/latest/meta-data/"])` for a store id that a victim Data Layer node has subscribed to [1](#0-0) .
2. Victim node observes the mirror coin, decodes and stores the URL unchecked via `coin_added`/`add_mirror` [5](#0-4) .
3. On the next sync cycle, `DataLayer.fetch_and_validate` selects this server and calls `insert_from_delta_file` → `download_file` → `http_download`, causing the victim node process to issue an HTTP GET to the attacker-chosen URL [6](#0-5) [7](#0-6) .

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
