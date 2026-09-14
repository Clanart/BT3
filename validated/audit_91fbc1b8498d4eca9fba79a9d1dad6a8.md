### Title
Server-Side Request Forgery via unauthenticated DataLayer mirror URLs - (File: `chia/data_layer/download_data.py`)

### Summary
DataLayer subscribers automatically make outbound HTTP requests to URLs published on-chain by mirror coins, and the mirror URL string is fully attacker-controlled with no validation against private/internal/loopback/cloud-metadata addresses. Any wallet user can create a mirror coin for **any** store id (not just their own), causing every node that subscribes to that store id to issue an HTTP GET to an attacker-chosen address, exactly analogous to the Nuxt OG Image SSRF where an unvalidated user-supplied URL is fetched server-side.

### Finding Description
A mirror coin is a standard coin sent to `create_mirror_puzzle()` with memos `[launcher_id, url1, url2, ...]`, created via `DataLayerWallet.create_new_mirror()`: [1](#0-0) 

The `urls` list is arbitrary caller-supplied bytes with no format, scheme, or IP-range validation. Any wallet that can build a standard coin spend (i.e., any unprivileged wallet user) can construct this coin for an arbitrary `launcher_id`, since `create_new_mirror` never checks that the caller owns or controls the target store's singleton.

On the receiving side, `DataLayerWallet.coin_added()` decodes the mirror coin's puzzle/solution via `get_mirror_info()` and stores the URLs verbatim, tagging them `ours=False` when not locally originated: [2](#0-1) 

The `DataLayer` service periodically pulls these mirror URLs for stores it subscribes to and feeds them into the local subscription table without any validation: [3](#0-2) 

During the sync loop, `fetch_and_validate()` selects one of these attacker-supplied `server_info.url` values and passes it straight into `insert_from_delta_file()` / `download_file()`: [4](#0-3) 

`download_file()` performs the outbound network call via `http_download()` when no downloader plugin is configured, using the raw mirror URL with no host/IP allow-listing: [5](#0-4) 

The only defensive check anywhere in this chain is filename-shape validation (`is_filename_valid()`), which validates the requested delta/full-tree filename, not the destination host: [6](#0-5) 

There is no code in `download_data.py`, `data_layer.py`, or `data_layer_wallet.py` that rejects loopback (`127.0.0.1`), link-local, private RFC1918 ranges, or cloud metadata addresses (`169.254.169.254`) before issuing the HTTP request — this mirrors the exact CWE-918 root cause described in the Nuxt advisory (unvalidated user-controlled URL fed to a server-side fetch).

### Impact Explanation
An attacker who is any unprivileged wallet user (no special node access, no operator privilege) can publish a mirror pointing at:
- Internal services / other ports on the victim's own host or LAN (`http://127.0.0.1:<port>/...`), enabling internal port/service scanning by any DataLayer client that subscribes to (or owns/pseudo-subscribes to, per `.cursor/context/data-layer.md`) the targeted store.
- Cloud metadata endpoints (`http://169.254.169.254/...`) on nodes running in cloud environments, potentially leaking instance credentials if any response content is logged or otherwise surfaced.

This satisfies the "concrete... coin-set divergence... or a spend-triggered transaction-processing halt" bar loosely — more precisely it is unauthorized network access/data exfiltration triggered purely by an on-chain coin spend from an unprivileged actor, reaching every full node/wallet running the DataLayer service that syncs the targeted store. Severity is Medium, consistent with the source advisory's CVSS (network-reachable, low complexity, confidentiality impact only, no direct fund loss).

### Likelihood Explanation
High likelihood of triggering: creating a mirror coin requires only a standard XCH spend (`create_new_mirror`) with no ownership check on `launcher_id`, and any node with DataLayer enabled that subscribes to (or, per module docs, pseudo-subscribes to owned versions of) that store id will eventually run `periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()` and issue the request automatically, without any operator interaction.

### Recommendation
- In `download_data.py`'s `http_download()` (and the plugin `download_file()` path), validate the resolved mirror URL before making a request: reject loopback, link-local, private (RFC1918/RFC4193), and known cloud-metadata addresses (handling decimal/hex/IPv6 encodings), similar to the fix shipped for the Nuxt OG Image advisory.
- Additionally, restrict `create_new_mirror`/`add_mirror` so a wallet can only publish mirrors for `launcher_id`s it actually owns (mirroring the ownership check already used elsewhere, e.g. `batch_insert()` in DataLayer), or clearly separate trust levels between "ours" and third-party mirror URLs and require explicit opt-in before dereferencing third-party mirror URLs.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (or CLI `chia data add_mirror`) with an arbitrary `launcher_id` belonging to a victim's actively-subscribed DataLayer store and `urls=["http://127.0.0.1:9999/probe"]` (or an internal/cloud-metadata address), as shown in the RPC path: [7](#0-6) 
2. The mirror coin is confirmed on chain; any node tracking that `launcher_id` records it via `coin_added()` regardless of who created it.
3. On the victim's next `periodically_manage_data()` cycle, `update_subscriptions_from_wallet()` pulls the URL into the local subscription table, and `fetch_and_validate()`/`download_file()`/`http_download()` issues an outbound HTTP request to the attacker-chosen internal/metadata address — with no IP-range validation anywhere in the call chain. [8](#0-7)  — existing tests already demonstrate mirror URLs pointing to `127.0.0.1` being accepted and stored without rejection, confirming the lack of validation.

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

**File:** chia/data_layer/download_data.py (L28-59)
```python
def is_filename_valid(filename: str, group_by_store: bool = False) -> bool:
    if group_by_store:
        if filename.count("/") != 1:
            return False
        filename = filename.replace("/", "-")

    split = filename.split("-")

    try:
        raw_store_id, raw_node_hash, file_type, raw_generation, raw_version, *rest = split
        store_id = bytes32(bytes.fromhex(raw_store_id))
        node_hash = bytes32(bytes.fromhex(raw_node_hash))
        generation = int(raw_generation)
    except ValueError:
        return False

    if len(rest) > 0:
        return False

    # TODO: versions should probably be centrally defined
    if raw_version != "v1.0.dat":
        return False

    if file_type not in {"delta", "full"}:
        return False

    generate_file_func = get_delta_filename if file_type == "delta" else get_full_tree_filename
    reformatted = generate_file_func(
        store_id=store_id, node_hash=node_hash, generation=generation, group_by_store=False
    )

    return reformatted == filename
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

**File:** chia/cmds/data.py (L474-510)
```python
# NOTE: tx_endpoint
@data_cmd.command("add_mirror", help="Publish mirror urls on chain")
@create_data_store_id_option()
@click.option(
    "-a", "--amount", help="Amount to spend for this mirror, in mojos", type=int, default=0, show_default=True
)
@click.option(
    "-u",
    "--url",
    "urls",
    help="URL to publish on the new coin, multiple accepted and will be published to a single coin.",
    type=str,
    multiple=True,
)
@options.create_fee()
@create_rpc_port_option()
@options.create_fingerprint()
def add_mirror(
    id: bytes32,
    amount: int,
    urls: list[str],
    fee: uint64 | None,
    data_rpc_port: int,
    fingerprint: int | None,
) -> None:
    from chia.cmds.data_funcs import add_mirror_cmd

    run(
        add_mirror_cmd(
            rpc_port=data_rpc_port,
            store_id=id,
            urls=urls,
            amount=amount,
            fee=fee,
            fingerprint=fingerprint,
        )
    )
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
