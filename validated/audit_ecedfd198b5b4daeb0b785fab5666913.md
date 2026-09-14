### Title
Unvalidated on-chain mirror URLs trigger unauthenticated outbound HTTP requests from Data Layer nodes (SSRF/DoS analog to CVE-2019-3871) - ([File: chia/data_layer/data_layer.py])

### Summary
The DataLayer service builds outbound HTTP requests to server/mirror URLs that originate from data an unprivileged party can put on-chain (a "mirror" coin's memo), without validating that the URL targets a safe, external endpoint. This mirrors the PowerDNS Remote-backend HTTP-connector bug (CVE-2019-3871): user-supplied data is used verbatim to construct an HTTP request/endpoint, letting an attacker make the service connect to an arbitrary (including internal) endpoint.

### Finding Description
Any wallet can create a "mirror" coin for **any** DataLayer launcher/store id (not just one it owns) by calling `DataLayerWallet.create_new_mirror()`, which puts the target `launcher_id` and a list of arbitrary `urls` into the coin's memo: [1](#0-0) 

When that coin is seen on chain, `DataLayerWallet.coin_added()` decodes the memo and, if the `launcher_id` is one the local wallet is tracking, stores the mirror (URLs) without any validation of scheme/host, regardless of who published it: [2](#0-1) 

The DataLayer service later pulls these wallet-tracked mirror URLs directly into its subscription server list: [3](#0-2) 

During the periodic sync loop, `fetch_and_validate()` iterates these (attacker-controlled) URLs and issues real outbound HTTP requests via `insert_from_delta_file()` → `download_file()` → `http_download()`, with no restriction preventing the URL from resolving to `127.0.0.1`, link-local/metadata addresses, or other internal hosts: [4](#0-3) [5](#0-4) 

The same unvalidated-URL pattern also drives plugin dispatch (`get_downloader`, `get_uploaders`, `upload_files`, `add_missing_files`), each of which POSTs to `plugin.url + "/..."` built from configuration/mirror data: [6](#0-5) [7](#0-6) 

The only "validation" present anywhere in this URL flow is stripping a trailing slash (`url.rstrip("/")`) in `subscribe()`/`update_subscriptions_from_wallet()` — there is no scheme allow-list, no private/loopback address blocking, and no bound on which store ids a URL-carrying mirror may target: [8](#0-7) 

This is functionally the same bug class as CVE-2019-3871: a remote/unprivileged party supplies data (there: a DNS query; here: an on-chain mirror memo) that is used, without adequate validation, to build an outbound HTTP request from the vulnerable service, allowing the attacker to redirect that service's connections to endpoints of their choosing.

### Impact Explanation
An attacker who tracks (or gets any user's node to track) a `launcher_id` can post a mirror transaction (small fee) whose memo contains URLs pointing at internal-only services (e.g. `http://127.0.0.1:<port>/...`, cloud metadata endpoints, or other hosts reachable only from the victim's network). The victim's DataLayer node will then, on its normal periodic `update_subscription()` cycle, autonomously issue HTTP GET/POST requests to that endpoint. This can be used to:
- Cause the node to hammer/interact with unintended internal services (denial-of-service against local RPC/services, or state-changing side effects if the internal endpoint accepts POST/GET actions).
- Potentially leak information indirectly through timing/response codes if the attacker can also observe some side channel, or crash the DataLayer sync loop's exception handling paths repeatedly.
No signature or authorization is required from the target store's owner — the mirror-posting wallet is a third party (any $ amount ≥ mojo fee suffices).

### Likelihood Explanation
Exploitation only requires an unprivileged wallet to construct a standard transaction (`dl_new_mirror`/`create_new_mirror`) targeting any known `launcher_id`, and for the victim to subscribe to or already track that store — both are normal, expected DataLayer usage patterns (subscribing to a store you don't own is the entire point of DL replication). Server operators running DataLayer with mirror/plugin support enabled are exposed with no special privilege needed by the attacker beyond posting a cheap on-chain transaction.

### Recommendation
- Validate all mirror/subscription/plugin URLs before use: enforce an allow-listed scheme (`http`/`https`), reject loopback/link-local/private (RFC1918)/multicast addresses unless explicitly configured for testing, and consider resolving DNS and re-checking the resolved IP before connecting (to prevent DNS-rebinding).
- Apply this validation both when storing mirrors (`DataLayerWallet.coin_added()`) and when consuming them for outbound requests (`DataLayer.update_subscriptions_from_wallet()`, `fetch_and_validate()`, `get_downloader()`, `get_uploaders()`, `upload_files()`, `add_missing_files()`).
- Consider requiring operator opt-in / explicit acknowledgement before a mirror discovered on-chain (as opposed to one manually configured) is used for actual network connections.

### Proof of Concept
1. Victim node runs DataLayer and subscribes to (tracks) a store with `launcher_id = L` owned by someone else (normal usage, e.g. `chia data subscribe`).
2. Attacker (any funded wallet) runs:
   `chia wallet <dl_new_mirror-equivalent RPC> --launcher-id L --urls http://127.0.0.1:8555/some_admin_endpoint --amount 1 --fee <mojos>`
   which invokes `DLNewMirror` → `DataLayerWallet.create_new_mirror()` (`chia/data_layer/data_layer_wallet.py:687`), publishing a mirror coin with the malicious URL in its memo.
3. Once confirmed, the victim's `DataLayerWallet.coin_added()` (`chia/data_layer/data_layer_wallet.py:775`) records the mirror for launcher `L` because it is tracked, with no URL validation.
4. On its next `periodically_manage_data()` cycle, the victim's `update_subscriptions_from_wallet()` (`chia/data_layer/data_layer.py:979`) pulls the malicious URL into the subscription's server list, and `fetch_and_validate()` (`chia/data_layer/data_layer.py:642`) causes an outbound HTTP request to `http://127.0.0.1:8555/...` from the victim node — demonstrating attacker-controlled SSRF triggered purely by an on-chain transaction from an unprivileged third party.

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

**File:** chia/data_layer/data_layer.py (L642-705)
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

**File:** chia/data_layer/data_layer.py (L805-833)
```python
            try:
                if len(uploaders) > 0:
                    request_json = {
                        "store_id": store_id.hex(),
                        "diff_filename": write_file_result.diff_tree.name,
                        "group_files_by_store": self.group_files_by_store,
                    }
                    if write_file_result.full_tree is not None:
                        request_json["full_tree_filename"] = write_file_result.full_tree.name

                    for uploader in uploaders:
                        self.log.info(f"Using uploader {uploader} for store {store_id.hex()}")
                        async with aiohttp.ClientSession(timeout=self.client_timeout) as session:
                            async with session.post(
                                uploader.url + "/upload",
                                json=request_json,
                                headers=uploader.headers,
                                timeout=self.client_timeout,
                            ) as response:
                                res_json = await response.json()
                                if res_json["uploaded"]:
                                    self.log.info(
                                        f"Uploaded files to {uploader} for store {store_id.hex()} "
                                        f"generation {publish_generation}"
                                    )
                                else:
                                    self.log.error(
                                        f"Failed to upload files to, will retry later: {uploader} : {res_json}"
                                    )
```

**File:** chia/data_layer/data_layer.py (L895-902)
```python
    async def subscribe(self, store_id: bytes32, urls: list[str]) -> Subscription:
        parsed_urls = [url.rstrip("/") for url in urls]
        subscription = Subscription(store_id, [ServerInfo(url, 0, 0) for url in parsed_urls])
        await self.wallet_rpc.dl_track_new(DLTrackNew(launcher_id=subscription.store_id))
        async with self.subscription_lock:
            await self.data_store.subscribe(subscription)
        self.log.info(f"Done adding subscription: {subscription.store_id}")
        return subscription
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
