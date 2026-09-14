### Title
Blind SSRF via unauthenticated DataLayer mirror coins causes arbitrary outbound HTTP requests from subscriber nodes and plugin services - ([File: chia/data_layer/data_layer.py])

### Summary
Any wallet user can create a DataLayer "mirror" coin advertising arbitrary URLs for *any* `launcher_id` (store id), without owning or controlling that store. Any other node that subscribes to that store will automatically pull the attacker-supplied URLs into its local mirror/server list and issue outbound HTTP(S) requests (and, if downloader/uploader plugins are configured, POST requests carrying `store_id`/`url` JSON) to those attacker-chosen destinations — a blind SSRF analogous to the WP Crontrol `wp_remote_request()` issue, but reachable by an unprivileged chain participant rather than a WordPress admin.

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a standard coin spend that sends XCH to `create_mirror_puzzle().get_tree_hash()` and encodes the `launcher_id` and arbitrary `urls` purely as **memos** on the created coin: [1](#0-0) 

Nothing in this puzzle or memo scheme requires the spender to own, be related to, or have any authority over the `launcher_id`/store being referenced — the mirror puzzle hash is generic and the `launcher_id` field is attacker-chosen free-form data. This is confirmed by the RPC-facing helper (`DataLayer.add_mirror`) which simply forwards caller-provided `urls`/`store_id` to the wallet without any ownership or ACL check: [2](#0-1) 

Any node that has subscribed to that `store_id` for replication purposes periodically calls `update_subscriptions_from_wallet()`, which reads all on-chain mirror records for that store (regardless of who created them) and merges their URLs into the local subscription/server list used for future downloads: [3](#0-2) 

`fetch_and_validate()` then randomly selects one of these attacker-supplied server URLs and issues outbound requests against it — either a direct HTTP GET via `http_download()`, or, when downloader plugins are configured, a JSON POST to `<plugin_url>/download` and to each configured downloader's `/handle_download` endpoint carrying the attacker's `url` value: [4](#0-3) [5](#0-4) [6](#0-5) 

Because `get_available_servers_for_store`/`fetch_and_validate` treat every stored mirror URL as an equally valid data source with no allow-list or scheme/host restriction, and because none of these code paths validate that the target host is not a loopback/link-local/internal address (the WP Crontrol analog to `wp_http_validate_url()`), a malicious mirror-coin creator can force any subscribing node — including operators running downloader/uploader plugin services that make privileged calls (e.g., S3, cloud metadata) — to issue requests to internal-only endpoints.

### Impact Explanation
This is a blind SSRF: the requesting node cannot observe the HTTP response content (only success/failure of the delta-file parse, or a boolean `handle_download`/`downloaded` JSON flag), mirroring the WP Crontrol advisory's "cannot see the HTTP response... nor time the response" characterization. Nonetheless it lets an unprivileged, unauthenticated on-chain actor (anyone able to submit a standard coin spend) cause arbitrary DataLayer-subscribing nodes, and any configured plugin services (which may run with elevated network/cloud privileges, e.g. `s3_plugin_service.py`), to originate requests toward internal infrastructure (metadata endpoints, internal admin panels, other services on localhost) chosen entirely by the attacker. It does not directly cause coin-set divergence, inflation, or fund theft, but it is a genuine confused-deputy / internal network probing and request-forgery primitive triggered purely by an attacker-created spend bundle, reachable from any node that subscribes to the targeted store.

### Likelihood Explanation
Likelihood is moderate: exploitation only requires the attacker to spend a small amount of XCH to create a mirror coin referencing any store id of their choosing (no special permissions, no cooperation from the store owner needed), and requires a victim to be actively subscribing to that store id (a normal DataLayer replication use case). Since mirror URLs are periodically re-synced (`update_subscription()`/`update_subscriptions_from_wallet`) on every subscription cycle, exploitation is persistent and repeatable as long as the malicious mirror coin remains unspent on-chain.

### Recommendation
- Validate mirror/server URLs before use in `update_subscriptions_from_wallet()` / `fetch_and_validate()` / `get_downloader()` / `download_file()`: reject loopback, link-local, private-range, and non-HTTP(S) targets, analogous to `wp_http_validate_url()`.
- Consider requiring mirror coins to be cryptographically tied to the actual store's singleton lineage (e.g., signed by or spent alongside the store's singleton) rather than accepting arbitrary free-form `launcher_id` memos from any spender.
- Apply the same URL validation to downloader/uploader plugin request targets (`d.url`, `uploader.url`) and add an explicit allow-list mechanism for operators, rather than trusting all on-chain-advertised mirror URLs equally.

### Proof of Concept
1. Attacker runs `chia data add_mirror -i <victim_store_id> -u http://169.254.169.254/latest/meta-data/ -a 1` (or any internal/loopback URL) using a wallet the attacker fully controls — no relationship to `<victim_store_id>` is required, per `data_layer.py:965-970` and `data_layer_wallet.py:687-703`.
2. The resulting mirror coin is confirmed on-chain like any standard coin spend.
3. A victim node that has run `chia data subscribe -i <victim_store_id> ...` periodically calls `update_subscriptions_from_wallet()` (`data_layer.py:979-985`), pulling in the attacker's URL as a legitimate server for that store.
4. On the next `fetch_and_validate()` cycle (`data_layer.py:642-694`), the victim node (or its configured downloader plugin, via `get_downloader()` at `data_layer.py:731-747`) issues an HTTP(S)/POST request to the attacker-chosen internal URL, with no way for the victim operator to have foreseen or blocked it via on-chain trust assumptions.

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

**File:** chia/data_layer/data_layer.py (L965-970)
```python
    async def add_mirror(self, store_id: bytes32, urls: list[str], amount: uint64, fee: uint64) -> None:
        if not urls:
            raise RuntimeError("URL list can't be empty")
        await self.wallet_rpc.dl_new_mirror(
            DLNewMirror(launcher_id=store_id, amount=amount, urls=urls, fee=fee, push=True), DEFAULT_TX_CONFIG
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
