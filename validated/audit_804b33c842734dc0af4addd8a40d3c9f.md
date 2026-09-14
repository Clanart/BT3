Confirmed: `download_data.py` performs no scheme/host allowlisting on mirror-derived URLs at all — no `urlparse`, no private-IP/localhost blocking anywhere in `download_data.py`, `data_layer.py` (the mirror/subscription path). Only the S3 plugin path checks `parse_result.scheme == "s3"`; the plain-HTTP path (`http_download`) has zero URL validation.

### Title
Unrestricted mirror-URL SSRF in DataLayer subscription fetch - (File: `chia/data_layer/download_data.py`)

### Summary
Any wallet holder can publish an on-chain "mirror" coin for **any** existing DataLayer store `launcher_id` with an attacker-chosen list of URLs, without owning that store. `DataLayerWallet.create_new_mirror()` places attacker-controlled URLs into coin memos with no ownership check, and any node that is subscribed to (or owns) that store will later call `http_download()` against those exact URLs with no scheme/host/IP validation, producing a classic SSRF: the victim node can be forced to issue GET requests to arbitrary internal hosts, ports, or cloud metadata endpoints.

### Finding Description
`DataLayerWallet.create_new_mirror()` [1](#0-0)  lets any wallet create a mirror coin for an arbitrary `launcher_id`, embedding a URL list in the coin memo. It performs no check that the caller owns or controls that `launcher_id`'s singleton — the RPC entrypoint `dl_new_mirror` in `chia/wallet/wallet_rpc_api.py` (lines 3255-3274) simply forwards `request.launcher_id` and `request.urls`. Any tracking wallet observes the mirror coin via `DataLayerWallet.coin_added()` and stores the URLs verbatim in `dl_store` [2](#0-1) .

The DataLayer service periodically calls `update_subscriptions_from_wallet()`, which copies these on-chain, attacker-supplied URLs into the local subscriptions table with no filtering: [3](#0-2) .

`fetch_and_validate()` then reads those URLs from `get_available_servers_for_store()` and passes them straight into `insert_from_delta_file()` → `download_file()` → `http_download()`: [4](#0-3) .

`http_download()` builds the request target as `server_info.url + "/" + filename` and issues an `aiohttp` GET with no scheme allowlist, no private/loopback/link-local IP blocklist, and no host validation whatsoever: [5](#0-4) .

This is the exact bug class in CVE-2020-14160: a remote-URL fetch feature (there, PDF-to-URL conversion; here, delta-file download) accepts a fully attacker-controlled URL and fetches it with no destination restriction, letting the caller pivot the server into probing/reading intranet or cloud-metadata resources.

Contrast this with the sibling S3-plugin path, which does at least check `parse_result.scheme == "s3"` and membership in a locally configured `store.urls` allowlist [6](#0-5)  — the plain HTTP path has no equivalent guard.

### Impact Explanation
Any wallet participant (no special privilege beyond a funded wallet to pay the mirror-coin fee) can force a victim DataLayer node — merely because it subscribes to or owns the targeted store — to make outbound HTTP GET requests to attacker-chosen destinations: internal-only services, RFC1918 addresses, `169.254.169.254` cloud metadata, or arbitrary ports on the victim's own host/network. This enables internal network reconnaissance/port-scanning from the operator's infrastructure and can be used to probe or interact with internal-only HTTP endpoints that would otherwise be unreachable from outside. Because subscribing to a store is a normal, expected DataLayer client action and mirror creation for a launcher_id requires no ownership relationship to that store, this is reachable by any unprivileged DataLayer/wallet user, matching the CVSS 7.5 severity of the reference CVE.

### Likelihood Explanation
High. Creating a mirror coin is a standard, permissionless `dl_new_mirror` wallet operation requiring only a fee payment; it needs no relationship to the target store's owner. Any counterparty publishing a DataLayer store that others subscribe to (a normal DataLayer usage pattern, e.g. via offers) can simultaneously or later publish malicious mirror URLs for that same store, and subscriber nodes will automatically pull and use them via the periodic `periodically_manage_data()` / `update_subscription()` loop.

### Recommendation
Add URL validation in `download_data.http_download()` (and in `data_layer.py` before persisting mirror URLs into subscriptions): enforce an `http`/`https` scheme allowlist and reject destinations resolving to loopback, link-local, private, or metadata-reserved IP ranges (equivalent to the SSRF mitigation eventually applied for Gotenberg). Optionally, require that mirror URLs be added only by the store's confirmed owner wallet, mirroring the ownership check already present in `batch_insert()`'s "owned by the DataLayer wallet" guard mentioned for other mutation paths.

### Proof of Concept
1. Attacker (any funded wallet) creates DataLayer store or targets an existing store's `launcher_id` known to have subscribers.
2. Attacker calls `dl_new_mirror` (`urls=["http://169.254.169.254/latest/meta-data/", "http://10.0.0.5:6379/"]`) via `chia/wallet/wallet_rpc_api.py::dl_new_mirror`, pushing a mirror coin on-chain.
3. Any DataLayer node subscribed to that `launcher_id` runs `update_subscriptions_from_wallet()` on its next `periodically_manage_data()` cycle, ingesting the malicious URLs into its subscriptions table.
4. On the next `fetch_and_validate()` cycle, `download_file()`/`http_download()` issues an outbound `aiohttp` GET to the attacker-chosen URL exactly as supplied, with no scheme/host restriction, demonstrating SSRF against the victim node's network position.

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

**File:** chia/data_layer/s3_plugin_service.py (L257-281)
```python
    async def download(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            url = data["url"]
            filename = data["filename"]
            group_files_by_store = data.get("group_files_by_store", False)
            max_delta_file_size = data.get("max_delta_file_size")
            if not isinstance(max_delta_file_size, int) or max_delta_file_size <= 0:
                max_delta_file_size = 250

            # filename must follow the DataLayer naming convention
            if not is_filename_valid(filename, group_files_by_store):
                return web.json_response({"downloaded": False})

            # Pull the store_id from the filename to make sure we only download for configured stores
            filename_store_id = bytes32.fromhex(filename[:64])
            parse_result = urlparse(url)
            should_download = False
            for store in self.stores:
                if store.id == filename_store_id and parse_result.scheme == "s3" and url in store.urls:
                    should_download = True
                    break

            if not should_download:
                return web.json_response({"downloaded": False})
```
