## Analysis

This maps to a genuine SSRF-class analog in the DataLayer subsystem: **any wallet user can publish arbitrary "mirror" URLs on-chain for *any* store, and a victim DataLayer node that is subscribed to (or owns) that store will automatically dereference those attacker-supplied URLs with outbound HTTP requests, with no validation of scheme, host, or destination.**

### Title
Unauthenticated on-chain mirror-URL injection lets any spend-bundle submitter force a victim DataLayer node into Blind SSRF - (File: `chia/data_layer/data_layer_wallet.py`)

### Summary
`DataLayerWallet.create_new_mirror()` spends a standard coin to the DataLayer mirror puzzle and embeds an arbitrary `launcher_id` plus arbitrary URL strings as coin memos, without verifying that the caller owns or controls that `launcher_id`/store. Any node that later reads that mirror (because it is subscribed to, or owns, that store id) will feed the attacker-controlled URL directly into `aiohttp` HTTP requests in `chia/data_layer/download_data.py` and `chia/data_layer/data_layer.py`, with no scheme/host validation, producing a blind SSRF against the victim's node process.

### Finding Description
`create_new_mirror()` builds the mirror-publishing transaction from user-supplied `launcher_id` and `urls` with zero ownership/ACL check: [1](#0-0) 

This is exposed to any wallet holder via the `dl_new_mirror` RPC and CLI, requiring only a fee-paying coin — not ownership of the target store's singleton: [2](#0-1) [3](#0-2) 

When a mirror coin is later seen on-chain, the URLs and `launcher_id` are extracted purely from puzzle memos and stored, again with no validation that the publisher is authorized for that `launcher_id`: [4](#0-3) 

Any DataLayer service (owner or subscriber of that store id) periodically pulls mirror URLs from wallet state and uses them as server candidates: [5](#0-4) 

Those URLs are then used directly as HTTP(S) fetch targets with no allow-listing of scheme or destination host/port: [6](#0-5) 

and in `get_downloader()`/`fetch_and_validate()`, the raw URL is also sent as JSON in POST bodies to configured plugins, and used to select/attempt an HTTP GET server: [7](#0-6) [8](#0-7) 

Because the mirror mechanism was designed as a generic "on-chain message board" (`P2_PARENT` mirror puzzle) rather than a store-scoped authorization mechanism, an attacker only needs to spend one of their own coins to inject a malicious URL/`launcher_id` pair; ownership of the referenced store is never checked at either the wallet or DataLayer service layer.

### Impact Explanation
Any unprivileged party who can submit a spend bundle can force a remote DataLayer-enabled full node (one that is subscribed to, or that owns, the targeted store id) to issue outbound HTTP/HTTPS requests to attacker-chosen addresses — including internal/private network addresses, cloud metadata endpoints, or arbitrary hosts/ports — via `http_download()` (`aiohttp.ClientSession.get`) or via plugin POST requests carrying the attacker URL in the JSON body. This is a classic blind SSRF: the victim node's process becomes an attacker-controlled network probe/pivot, and repeated large/slow responses or connection attempts can also degrade DataLayer sync availability for that node.

### Likelihood Explanation
Likelihood is high for any environment where DataLayer subscriptions are used across independently-operated nodes (the intended cross-organization use case for DataLayer). The attack requires only a minimal on-chain spend (mirror coin creation), no cooperation from the store owner, and no elevated privileges — it is fully reachable from a standard wallet action / spend bundle submission.

### Recommendation
- Require that `create_new_mirror()`/`dl_new_mirror` only succeed when the caller can prove ownership/authorization of the target `launcher_id` (e.g., signing with a key tied to the singleton, or requiring the mirror-publishing coin to be spent alongside the singleton itself), rather than accepting arbitrary `launcher_id` values in memos.
- At the consuming side (`data_layer.py`, `download_data.py`), validate mirror URLs before use: enforce an allow-listed scheme (`https` only, or restrict to configured downloader schemes like `s3://`), reject loopback/link-local/private IP ranges unless explicitly configured, and make outbound fetches go through a policy-enforcing HTTP client rather than raw `aiohttp` requests to attacker-controlled strings.
- Treat mirror data as fully untrusted external input at ingestion time (as is already done for downloaded file *contents*), and apply the same rigor to the *URLs themselves*.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (RPC) or `chia data add_mirror` (CLI) with:
   - `launcher_id` = the store id of a victim's DataLayer store (public, visible on-chain),
   - `urls` = `["http://169.254.169.254/latest/meta-data/", "http://internal-admin.victim-lan:8080/debug"]`,
   - a small `amount`/`fee`.
2. This creates a coin to `create_mirror_puzzle().get_tree_hash()` with memos `[launcher_id, url1, url2]` — no signature/ownership tie to `launcher_id` is required, per `chia/data_layer/data_layer_wallet.py:687-703`.
3. Once confirmed, any node subscribed to/owning that `launcher_id` picks up the mirror via `coin_added()` (`chia/data_layer/data_layer_wallet.py:775-800`) and `update_subscriptions_from_wallet()` (`chia/data_layer/data_layer.py:979-985`).
4. On its next sync cycle, that victim node's `fetch_and_validate()`/`http_download()` issues an HTTP GET (or plugin POST containing the URL) to the attacker-chosen address (`chia/data_layer/data_layer.py:642-706`, `chia/data_layer/download_data.py:298-323`), demonstrating blind SSRF from the victim node.

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

**File:** chia/data_layer/data_layer.py (L642-706)
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
                if success:
                    self.log.info(
                        f"Finished downloading and validating {store_id}. "
                        f"Wallet generation saved: {singleton_record.generation}. "
                        f"Root hash saved: {singleton_record.root}."
                    )
                    break
            except aiohttp.client_exceptions.ClientConnectorError:
                self.log.warning(f"Server {url} unavailable for {store_id}.")
            except Exception as e:
                self.log.warning(f"Exception while downloading files for {store_id}: {e} {traceback.format_exc()}.")

```

**File:** chia/data_layer/data_layer.py (L731-747)
```python
    async def get_downloader(self, store_id: bytes32, url: str) -> PluginRemote | None:
        request_json = {"store_id": store_id.hex(), "url": url}
        for d in self.downloaders:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(
                        d.url + "/handle_download",
                        json=request_json,
                        headers=d.headers,
                        timeout=self.client_timeout,
                    ) as response:
                        res_json = await response.json()
                        if res_json["handle_download"]:
                            return d
                except Exception as e:
                    self.log.error(f"get_downloader could not get response: {type(e).__name__}: {e}")
        return None
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
