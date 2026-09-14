### Title
Server-Side Request Forgery via unvalidated Data Layer mirror URLs - (File: chia/data_layer/data_layer.py, chia/data_layer/download_data.py)

### Summary
Any wallet user who owns a DataLayer store can publish arbitrary "mirror" URLs on-chain via the `add_mirror` RPC. These URLs are later pulled by any other Data Layer client that subscribes to (or tracks) that store, stored as subscription server URLs, and then fetched with unauthenticated, unvalidated outbound HTTP GET requests during normal sync (`fetch_and_validate` → `insert_from_delta_file` → `download_file`/`http_download`). No validation prevents the URL from pointing at loopback, link-local, or other internal/private network addresses, allowing blind SSRF against a victim's internal network — the same bug class as GHSA-gcvv-72q8-9v76 (Ghost Admin SSRF in image fetching).

### Finding Description
`DataLayer.add_mirror()` lets a store owner publish arbitrary URLs on-chain through the wallet's DL mirror coin mechanism [1](#0-0) , implemented in the wallet RPC as `dl_new_mirror` [2](#0-1) . Any other node that is subscribed to / tracking that singleton periodically calls `update_subscriptions_from_wallet()`, which reads these mirror URLs from wallet RPC and inserts them directly into the local subscriptions table with no host/URL validation (no scheme restriction, no private-IP/loopback filtering) [3](#0-2) [4](#0-3) .

During the periodic sync loop, `fetch_and_validate()` iterates these server URLs and calls `insert_from_delta_file`, passing the untrusted URL straight through to `download_file()` [5](#0-4) . When no downloader plugin is configured (the default, plain-HTTP path), `download_file()` calls `http_download()` directly with the attacker-controlled `server_info.url`, and this raises on any error but otherwise performs the fetch with only filename validation — there is no check that the target host is not localhost/internal [6](#0-5) . The RPC-level `subscribe` endpoint accepts caller-supplied URLs as well, and both callers (RPC caller and on-chain mirror propagation) feed the same unauthenticated HTTP client path [7](#0-6) .

### Impact Explanation
An attacker who creates or controls a DataLayer store can publish a malicious mirror URL (e.g., `http://127.0.0.1:<internal-port>/...` or `http://169.254.169.254/...`) on-chain. Any other node's DataLayer service that later subscribes to or tracks that store (a routine, low-friction action for Data Layer/offer participants) will automatically issue outbound blind HTTP GET requests to that URL as part of its normal sync loop, without operator interaction. This lets a remote unprivileged actor probe or interact with services on the victim's internal network/localhost (e.g., other local RPC ports, cloud metadata endpoints), matching the "blind SSRF used to probe internal hosts/ports" impact of the referenced Ghost advisory. This is a Medium-severity SSRF (CWE-918) matching CVSS AV:N/AC:L/PR:H(analogous "must interact with malicious data")/UI:N/S:C/C:L class impact.

### Likelihood Explanation
Likelihood is moderate: exploitation requires the victim's DataLayer node to be actively subscribed to (or tracking) the attacker's store — this is the normal, expected mode of DataLayer usage between offer counterparties or public mirror consumers, and requires no special privilege from the attacker beyond owning/publishing a store and mirror coin, both permissionless on-chain actions.

### Recommendation
Validate and restrict mirror/server URLs before use for outbound fetches: enforce an allow-list of schemes (http/https), resolve and reject requests to loopback, link-local, private (RFC1918), and other non-routable/internal address ranges in `http_download`/`download_file` (and in `update_subscriptions_from_wallet`/`subscribe`), and consider requiring explicit operator opt-in for URLs resolving to internal address space.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror` with `urls=["http://127.0.0.1:<victim-internal-port>/"]` [8](#0-7) .
2. Victim's DataLayer node, subscribed/tracking the attacker's store, runs `update_subscriptions_from_wallet()` and stores the attacker URL as a subscription server [3](#0-2) .
3. On the next sync cycle, `fetch_and_validate()` picks this server and calls `insert_from_delta_file` → `download_file` → `http_download`, causing the victim node to issue a blind HTTP GET to the internal target [9](#0-8) [6](#0-5) .

### Citations

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

**File:** chia/data_layer/data_store.py (L1668-1704)
```python
    async def update_subscriptions_from_wallet(self, store_id: bytes32, new_urls: list[str]) -> None:
        async with self.db_wrapper.writer() as writer:
            cursor = await writer.execute(
                "SELECT * FROM subscriptions WHERE from_wallet == 1 AND tree_id == :tree_id",
                {
                    "tree_id": store_id,
                },
            )
            old_urls = [row["url"] async for row in cursor]
            cursor = await writer.execute(
                "SELECT * FROM subscriptions WHERE from_wallet == 0 AND tree_id == :tree_id",
                {
                    "tree_id": store_id,
                },
            )
            from_subscriptions_urls = {row["url"] async for row in cursor}
            additions = {url for url in new_urls if url not in old_urls}
            removals = [url for url in old_urls if url not in new_urls]
            for url in removals:
                await writer.execute(
                    "DELETE FROM subscriptions WHERE url == :url AND tree_id == :tree_id",
                    {
                        "url": url,
                        "tree_id": store_id,
                    },
                )
            for url in additions:
                if url not in from_subscriptions_urls:
                    await writer.execute(
                        "INSERT INTO subscriptions(tree_id, url, ignore_till, num_consecutive_failures, from_wallet) "
                        "VALUES (:tree_id, :url, 0, 0, 1)",
                        {
                            "tree_id": store_id,
                            "url": url,
                        },
                    )

```

**File:** chia/data_layer/download_data.py (L130-145)
```python

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

**File:** chia/data_layer/data_layer_rpc_api.py (L360-371)
```python
    async def subscribe(self, request: dict[str, Any]) -> EndpointResult:
        """
        subscribe to singleton
        """
        store_id = request.get("id")
        if store_id is None:
            raise Exception("missing store id in request")

        store_id_bytes = bytes32.from_hexstr(store_id)
        urls = request.get("urls", [])
        await self.service.subscribe(store_id=store_id_bytes, urls=urls)
        return {}
```

**File:** chia/data_layer/data_layer_rpc_api.py (L468-475)
```python
    async def add_mirror(self, request: dict[str, Any]) -> EndpointResult:
        store_id = request["id"]
        id_bytes = bytes32.from_hexstr(store_id)
        urls = request["urls"]
        amount = request["amount"]
        fee = get_fee(self.service.config, request)
        await self.service.add_mirror(id_bytes, urls, amount, fee)
        return {}
```
