### Title
Authenticated SSRF in Data Layer mirror URL download via unvalidated `http_download` - ([File: chia/data_layer/download_data.py])

### Summary
Any wallet holder can publish an arbitrary URL to the chain as a DataLayer "mirror" for a store via `dl_new_mirror`/`chia data add_mirror`. Any other user who subscribes to that store will have their DataLayer service fetch data from that attacker-controlled URL using the shared `aiohttp` client with no destination filtering, mirroring the Penpot CVE-2026-45806 pattern of unvalidated URL fetch reaching internal-only endpoints.

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet (an "authenticated" but otherwise unprivileged actor, analogous to Penpot's "authenticated file editor") put an arbitrary string list of URLs on-chain in the coin memo for a mirror coin, with no URL/scheme/host validation: [1](#0-0) 

This is exposed via the `dl_new_mirror` RPC endpoint / `chia data add_mirror` CLI, again with no validation of the `urls` field: [2](#0-1) [3](#0-2) [4](#0-3) 

Any other node that has subscribed to that store id (a normal, low-friction DataLayer action) will sync these mirror URLs into its local server list via `update_subscriptions_from_wallet`, and later `fetch_and_validate()` shuffles and iterates these attacker-controlled URLs, calling `insert_from_delta_file()` for each: [5](#0-4) 

`insert_from_delta_file()` calls `download_file()`, which — when no downloader plugin is configured (the default case) — calls `http_download()`: [6](#0-5) 

`http_download()` performs an `aiohttp` GET directly against `server_info.url + "/" + filename` using the shared client session, with no validation that the URL is not pointing at localhost, link-local/metadata addresses, or other internal-only endpoints, and honors an optional configured proxy but performs no SSRF-specific destination filtering: [7](#0-6) 

This is structurally identical to the Penpot bug class described in the report: a user-controlled URL from a lower-trust actor (`mirror` creator) flows unfiltered into a shared HTTP client (`http_download`) on a higher-trust component (the victim's DataLayer full node service), which can be pointed at internal-only endpoints reachable from that host.

### Impact Explanation
An attacker who gets a victim to subscribe to (or who owns/mirrors into) a DataLayer store can force the victim's DataLayer node process to issue outbound HTTP GET requests to arbitrary internal or loopback addresses (e.g., internal RPC ports, cloud metadata endpoints, other services on the victim's private network) at a time and cadence chosen by the attacker (mirror URLs are re-tried/retried with backoff via `server_misses_file`). While the response body must parse as a delta file to have further downstream effect, the request itself already achieves SSRF (port scanning internal network, probing for services, hitting unauthenticated internal admin endpoints) purely from the GET being issued, and error/timing behavior can be used to fingerprint internal network topology. This aligns with Medium/High severity under the "reachable by an unprivileged submitter/wallet action" scope of this scan.

### Likelihood Explanation
Likelihood is moderate-to-high: creating a mirror coin requires only a wallet with some XCH for the coin/fee and no special permission (`create_new_mirror` is a standard-wallet spend), and getting a victim to subscribe to an attacker's store is a normal, expected DataLayer workflow (subscribing to third-party stores is the entire purpose of the mirror/subscription feature). No additional social engineering beyond "subscribe to this data store" is required.

### Recommendation
- Validate and restrict mirror URLs both at wallet publish time (`create_new_mirror`/`dl_new_mirror`) and, more importantly, at consumption time in `http_download()`/`download_file()`: reject URLs resolving to loopback, link-local, private, or other non-routable/internal address ranges before issuing the request (defense in depth, since publish-time validation is bypassable by a non-Chia client crafting the mirror coin memo directly).
- Enforce an allow-list of schemes (`http`/`https` only, already implicit but not enforced) and require DNS resolution checks against private/internal IP ranges at request time, not just before construction of the URL string.
- Consider isolating outbound DataLayer mirror fetches through a dedicated egress-restricted HTTP client/proxy rather than the general-purpose `aiohttp.ClientSession()`.

### Proof of Concept
1. Attacker creates a DataLayer store and, using a wallet with DL support, calls `dl_new_mirror` (or `chia data add_mirror --id <store_id> -u http://169.254.169.254/latest/meta-data/ -a 1`) to publish a mirror URL pointing at an internal-only endpoint reachable from the victim's network (loopback service, cloud metadata endpoint, internal admin RPC port), per `create_new_mirror()` in `chia/data_layer/data_layer_wallet.py:687-703`.
2. Victim runs a DataLayer node and subscribes to the attacker's `store_id` (standard usage of the feature).
3. Victim's `DataLayer.update_subscriptions_from_wallet()` (`chia/data_layer/data_layer.py:979-985`) picks up the malicious mirror URL from chain state.
4. On the next sync cycle, `fetch_and_validate()` (`chia/data_layer/data_layer.py:642-694`) selects the malicious `server_info.url` and calls `insert_from_delta_file()` → `download_file()` → `http_download()` (`chia/data_layer/download_data.py:110-145,298-324`), which issues an outbound HTTP GET from the victim's process directly to the attacker-chosen internal URL, demonstrating SSRF.

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

**File:** chia/data_layer/data_layer.py (L965-970)
```python
    async def add_mirror(self, store_id: bytes32, urls: list[str], amount: uint64, fee: uint64) -> None:
        if not urls:
            raise RuntimeError("URL list can't be empty")
        await self.wallet_rpc.dl_new_mirror(
            DLNewMirror(launcher_id=store_id, amount=amount, urls=urls, fee=fee, push=True), DEFAULT_TX_CONFIG
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
