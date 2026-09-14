### Title
DataLayer SSRF via unvalidated mirror URLs fetched by `http_download` / `get_uploaders` / `get_downloader` - ([File: chia/data_layer/download_data.py])

### Summary
Any wallet holder can publish an on-chain "mirror" for a DataLayer store containing arbitrary attacker-chosen URLs via `DataLayerWallet.create_new_mirror()` / RPC `dl_new_mirror`. Other DataLayer clients that subscribe to that store ingest these URLs unvalidated and later cause their local `data_layer` service to issue outbound HTTP requests to them, with no restriction on scheme, host, or destination address (including intranet/loopback/cloud-metadata addresses).

### Finding Description
Mirrors are on-chain coins created by `DataLayerWallet.create_new_mirror()`, which stores a `launcher_id` and a list of raw `url` byte-strings in the coin's memos with **no validation** of the URL content: [1](#0-0) 

Any full node peer that observes this mirror coin (`coin_added`) decodes and stores the URLs in `dl_store` for the referenced `launcher_id`, again without any host/scheme validation: [2](#0-1) 

A DataLayer node that has subscribed to that store id pulls these mirror URLs into its subscription table via `update_subscriptions_from_wallet`: [3](#0-2) 

`fetch_and_validate()` then iterates the stored `servers_info` (populated from the untrusted mirror URLs) and passes each URL into `insert_from_delta_file` → `download_file` → `http_download`, which performs a plain `aiohttp` GET directly against the attacker-supplied URL with no allow-list, no blocking of private/loopback/link-local ranges, and no SSRF protections: [4](#0-3) [5](#0-4) 

The same untrusted-URL pattern also drives outbound POST requests to uploader/downloader "plugin" URLs and to `get_downloader`, both of which build requests to attacker/config-influenced URLs: [6](#0-5) [7](#0-6) 

This exactly matches the CVE-2017-5518 bug class: a feature that accepts a URL from an untrusted actor and later performs a server-side fetch of that URL without validating that it does not target intranet/private addresses.

### Impact Explanation
Any user (no special privileges) can publish a mirror URL pointing at an intranet address, a loopback service, or a cloud metadata endpoint (e.g. `169.254.169.254`). Every other DataLayer node subscribed to that store — including operators running DataLayer on cloud infrastructure — will have its own service perform HTTP GET/POST requests to that address whenever it syncs (`fetch_and_validate`, `upload_files`, `add_missing_files`, `get_downloader`, `get_uploaders`). This can be used to probe/exfiltrate internal network services, hit cloud instance-metadata services to steal credentials, or pivot to attack other internal systems reachable from the victim host — a classic high-severity SSRF impact, reachable purely through a normal, low-cost DataLayer/wallet action (creating a mirror coin) that any wallet user can perform.

### Likelihood Explanation
High. Creating a mirror requires only a standard wallet spend (fee-paying, no special permission) via `dl_new_mirror`/CLI `add_mirror`, and any DataLayer client that subscribes to (or is a mirror/uploader-consumer of) the affected store will automatically fetch the URL during its normal periodic sync loop (`manage_data_interval`) with no user interaction beyond having subscribed to the store, which is standard DataLayer usage.

### Recommendation
Validate and restrict mirror/plugin URLs before they are persisted and before they are dereferenced by `http_download`/`get_downloader`/`get_uploaders`/upload-plugin POST calls: enforce an allow-list of schemes (http/https only), resolve and reject hostnames/IPs that fall into private, loopback, link-local, or metadata-service ranges (unless explicitly operator-configured), and consider making outbound mirror fetching opt-in/config-gated per operator rather than automatically following untrusted on-chain URLs.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `dl_new_mirror` (or CLI `chia data add_mirror`) with `urls=["http://169.254.169.254/latest/meta-data/"]` (or an internal RFC1918 address), paying only a normal transaction fee.
2. Victim operator's DataLayer node subscribes to (or already tracks) the same `store_id` and runs its normal sync loop.
3. `update_subscriptions_from_wallet` pulls the malicious URL into the victim's subscription table; on the next `fetch_and_validate()` cycle, `http_download` issues an unauthenticated GET to the attacker-chosen internal/metadata address from the victim's host.
4. Any response content/behavior differences (timing, errors, successful download) let the attacker infer internal network reachability or exfiltrate data if the endpoint returns sensitive content that gets written to the delta-file path and can later be inspected via logs/errors. [8](#0-7)

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

**File:** chia/data_layer/data_layer.py (L965-977)
```python
    async def add_mirror(self, store_id: bytes32, urls: list[str], amount: uint64, fee: uint64) -> None:
        if not urls:
            raise RuntimeError("URL list can't be empty")
        await self.wallet_rpc.dl_new_mirror(
            DLNewMirror(launcher_id=store_id, amount=amount, urls=urls, fee=fee, push=True), DEFAULT_TX_CONFIG
        )

    async def delete_mirror(self, coin_id: bytes32, fee: uint64) -> None:
        await self.wallet_rpc.dl_delete_mirror(DLDeleteMirror(coin_id=coin_id, fee=fee, push=True), DEFAULT_TX_CONFIG)

    async def get_mirrors(self, store_id: bytes32) -> list[Mirror]:
        mirrors: list[Mirror] = (await self.wallet_rpc.dl_get_mirrors(DLGetMirrors(launcher_id=store_id))).mirrors
        return [mirror for mirror in mirrors if mirror.urls]
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

**File:** chia/data_layer/data_layer.py (L1387-1403)
```python
    async def get_uploaders(self, store_id: bytes32) -> list[PluginRemote]:
        uploaders = []
        for uploader in self.uploaders:
            async with aiohttp.ClientSession(timeout=self.client_timeout) as session:
                try:
                    async with session.post(
                        uploader.url + "/handle_upload",
                        json={"store_id": store_id.hex()},
                        headers=uploader.headers,
                        timeout=self.client_timeout,
                    ) as response:
                        res_json = await response.json()
                        if res_json["handle_upload"]:
                            uploaders.append(uploader)
                except Exception as e:
                    self.log.error(f"get_uploader could not get response {e}")
        return uploaders
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
