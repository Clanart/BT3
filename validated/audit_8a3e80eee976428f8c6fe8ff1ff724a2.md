### Title
Data Layer SSRF via unauthenticated on-chain mirror URLs followed without redirect/host restrictions - (File: chia/data_layer/download_data.py)

### Summary
Any wallet user can create an on-chain "mirror" record for **any** DataLayer store (not just their own), embedding attacker-controlled URLs. Any node subscribed to that store's DataLayer singleton will fetch delta files from those URLs using an unauthenticated `aiohttp` client that follows redirects by default and performs no hostname/IP validation, allowing SSRF against internal services, cloud metadata endpoints, or other local-network resources reachable from the DataLayer process — the same bug class as the Craft CMS GraphQL asset-mutation SSRF (redirect-based blocklist/validation bypass), except here there isn't even an initial blocklist to bypass.

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a mirror coin whose memo simply encodes `[launcher_id, *urls]` with **no check that the caller owns or controls `launcher_id`**: [1](#0-0) 

This is exposed directly via the wallet RPC `dl_new_mirror`, which any wallet-RPC caller can invoke for an arbitrary `launcher_id`: [2](#0-1) 

Any DataLayer service subscribed to that store id periodically calls `update_subscriptions_from_wallet()`, which pulls mirror URLs straight from the wallet's on-chain mirror records with no filtering: [3](#0-2) 

`fetch_and_validate()` then iterates `servers_info` built from those URLs and calls `insert_from_delta_file` → `download_file` → `http_download`: [4](#0-3) 

`http_download` issues an `aiohttp.ClientSession().get(server_info.url + "/" + filename, ...)` request with no hostname/IP allow-list, no SSRF blocklist, and default `allow_redirects=True` (not disabled), so the attacker-supplied URL — or a URL that 302-redirects — is fetched directly by the DataLayer node process: [5](#0-4) 

This mirrors the Craft CMS advisory's root cause exactly: an externally supplied URL is trusted for an outbound fetch, and HTTP redirects are followed without re-validating the resolved destination — except here there is no validation step at all, and worse, the URL is not even required to be "your own store's" URL since `create_new_mirror` never verifies `launcher_id` ownership.

### Impact Explanation
A single on-chain transaction (a `dl_new_mirror` mirror-coin spend, submittable by any wallet holder) can cause any DataLayer node that subscribes to the targeted store to make outbound HTTP requests to attacker-chosen or redirect-chosen destinations (e.g. `169.254.169.254` cloud metadata, internal RPC ports, other localhost services). This can leak cloud credentials/metadata, probe/attack internal network services, or be used to fingerprint infrastructure — classic CWE-918 SSRF impact, reachable purely from a submitted spend bundle/wallet action with no operator cooperation beyond running a normal DataLayer subscription.

### Likelihood Explanation
Likelihood is moderate-to-high for any deployment running a DataLayer service with subscriptions: mirror creation is a permissionless on-chain action for any `launcher_id`, requires only a standard XCH spend plus fee, and the download path performs no destination validation at all. This is a straightforward analog to the referenced advisory's redirect-bypass pattern, just without needing the redirect trick since no blocklist exists in the first place.

### Recommendation
- In `create_new_mirror`/`dl_new_mirror`, restrict mirror registration to launcher IDs the wallet actually owns, or otherwise scope subscription-time trust of mirror URLs.
- In `http_download` (chia/data_layer/download_data.py), disable automatic redirect following (`allow_redirects=False`) or re-validate the resolved IP after each redirect hop, and add a hostname/IP allow/deny list (reject private/loopback/link-local ranges such as `169.254.169.254`) before connecting, consistent with SSRF mitigations applied elsewhere (e.g. peer host validation in `chia/server/address_manager.py`).

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` RPC with `launcher_id` set to a victim's DataLayer store id (no ownership check enforced) and `urls=["http://attacker.com/redirect"]`, pushing the resulting spend bundle to the mempool.
2. `attacker.com/redirect` responds with `302 Location: http://169.254.169.254/latest/meta-data/` (or any internal address).
3. Once confirmed, any DataLayer node subscribed to that store calls `update_subscriptions_from_wallet()` → picks up the new mirror URL → `fetch_and_validate()` → `download_file()` → `http_download()`.
4. `aiohttp.ClientSession().get(...)` follows the redirect (default behavior, no validation), and the DataLayer node's HTTP client fetches the internal/metadata resource, potentially persisting/exposing its contents through the delta-file processing pipeline.

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

**File:** chia/wallet/wallet_rpc_api.py (L3255-3277)
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

        # tx_endpoint will take care of default values here
        return DLNewMirrorResponse(unsigned_transactions=[], transactions=[])
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

**File:** chia/data_layer/download_data.py (L298-319)
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
```
