### Title
SSRF via unauthenticated on-chain DataLayer mirror URL causing arbitrary outbound HTTP fetch during sync - (File: chia/data_layer/data_layer_wallet.py, chia/data_layer/download_data.py)

### Summary
Chia's DataLayer "mirror" mechanism lets any wallet holder publish an arbitrary URL on-chain, associated with any `launcher_id` (DataLayer store), with no validation that the caller owns that store. Other DataLayer nodes that later sync that store will pick up the attacker-controlled URL and issue an outbound HTTP request to it during `fetch_and_validate`, mirroring the "server retrieves a remote URI supplied by an unprivileged caller" bug class in the reported 2FAuth SSRF.

### Finding Description
`DataLayerWallet.create_new_mirror` builds a coin whose puzzle is the generic mirror puzzle and whose memo simply contains the caller-supplied `launcher_id` and `urls`, with no check that the spending wallet actually owns/controls that `launcher_id`/store: [1](#0-0) 

The RPC entrypoint `dl_new_mirror` passes the request straight through without any ownership validation of `launcher_id`: [2](#0-1) 

Any unprivileged wallet user can therefore submit a standard transaction that creates a mirror coin advertising an arbitrary URL (e.g. `http://169.254.169.254/`, `http://internal-host:port/...`, or a listener under attacker control) for a `launcher_id` belonging to someone else's DataLayer store. Because a mirror is just a coin with a specific puzzle hash and standard memo format, this is reachable purely via a submitted spend bundle/wallet RPC call — it requires no special permission.

When another node's DataLayer service (`DataLayer.fetch_and_validate`) is syncing that store, it reads the mirror's URL from `get_available_servers_for_store` and issues an outbound request to it via `download_file`/`http_download`: [3](#0-2) [4](#0-3) [5](#0-4) 

`http_download` performs `session.get(server_info.url + "/" + filename, ...)` with no scheme allow-list and no restriction preventing internal/loopback/link-local addresses, matching the SSRF primitive in the report (server making an outbound request to an attacker-chosen URI). The only content-side check is `is_filename_valid`, which validates the *requested filename* format, not the destination host/scheme: [6](#0-5) 

### Impact Explanation
An unprivileged wallet user can force any other participant's DataLayer node to make outbound HTTP requests to attacker-chosen hosts (including internal/private network addresses), and the response body is written to disk and parsed as delta/full-tree file content via `write_files_for_root`/`insert_from_delta_file`. This is a genuine SSRF vector reachable from a single submitted transaction (attacker never needs to own the target store), analogous to the 2FAuth preview endpoint that fetched attacker-supplied URIs server-side. Impact is limited to disclosure/probing of internal network reachability and potential resource abuse (bounded by `max_delta_file_size`); it does not directly enable unauthorized coin movement, inflation, or consensus divergence, which caps this at a network/SSRF-class issue for a component (`chia/data_layer`) that is optional and consumed by users who deliberately run DataLayer with subscriptions to third-party stores.

### Likelihood Explanation
Likelihood is high for the trigger step: creating a mirror coin for an arbitrary `launcher_id` is a simple, unauthenticated wallet operation available to any XCH holder, and no code path checks ownership of the target `launcher_id` before publishing the URL on-chain. However, actual SSRF only fires when a *victim* DataLayer node is actively syncing/subscribed to the targeted store and iterates the malicious mirror as a download server, which requires the victim to already be tracking that specific store.

### Recommendation
- Validate that `create_new_mirror`/`dl_new_mirror` is only permitted for stores the wallet actually owns, or at minimum warn/require explicit opt-in before a node fetches from mirrors it did not select itself.
- In `http_download`/`download_file`, restrict destination scheme (`http`/`https` only) and reject requests to private/loopback/link-local IP ranges (SSRF allow/deny-listing), similar to standard SSRF mitigations.
- Consider requiring signed/verifiable server metadata (e.g., pinned host allow-list per store) rather than trusting arbitrary on-chain memo strings as fetchable URLs.

### Proof of Concept
1. Attacker (any wallet holder) calls `dl_new_mirror` RPC with `launcher_id` = victim's known public DataLayer store ID and `urls=["http://169.254.169.254/latest/meta-data/"]`, and pushes the resulting spend bundle to the mempool — no wallet ownership of `launcher_id` is required (`chia/wallet/wallet_rpc_api.py:3255-3274`, `chia/data_layer/data_layer_wallet.py:687-704`).
2. Once confirmed, the mirror is discoverable by any node subscribed to that store via `get_mirrors`/`get_available_servers_for_store`.
3. When a victim node running `chia data` with a subscription to that `store_id` next calls `fetch_and_validate` to sync, it will select the malicious mirror URL and invoke `http_download`, causing the victim's node to issue a GET request to `http://169.254.169.254/latest/meta-data/` (`chia/data_layer/data_layer.py:642-694`, `chia/data_layer/download_data.py:298-324`), with the response written into the victim's local delta-file cache path.

### Citations

**File:** chia/data_layer/data_layer_wallet.py (L687-704)
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
