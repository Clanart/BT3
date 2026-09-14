### Title
DataLayer mirror URLs allow any wallet holder to trigger arbitrary outbound HTTP requests from other nodes' DataLayer service (SSRF) - ([File: chia/data_layer/data_layer.py])

### Summary
Chia's DataLayer feature lets any wallet owner publish an on-chain "mirror" coin whose only user-controlled payload is a list of arbitrary URL strings. Every other node that subscribes to or owns that same `store_id` will later read those URLs from wallet RPC and use them, unauthenticated and unvalidated, to issue outbound `aiohttp` POST/GET requests (to plugin `/handle_download`, `/handle_upload`, `/download` endpoints, and direct HTTP downloads of delta/full-tree files) with no scheme allow-list or destination network restriction — the same bug class as the Nautobot Webhook SSRF (GHSA-c35q-vxrp-ph26): a low-privilege actor supplies an unauthenticated network destination that a privileged server-side component then blindly contacts.

### Finding Description
Any wallet user can call the `add_mirror` RPC with an arbitrary list of URL strings for a `store_id`, with no server-side validation of scheme or host: [1](#0-0) 

This calls `dl_new_mirror`, which pushes an on-chain mirror coin encoding the URL list; the wallet RPC layer performs no URL validation either: [2](#0-1) 

Any other node that is subscribed to (or owns) the same store id will, during its periodic sync loop, pull these mirror URLs directly from the chain state via wallet RPC and persist them as subscription server URLs with no filtering: [3](#0-2) [4](#0-3) 

The DataLayer service then uses these attacker-supplied URLs to make unauthenticated outbound HTTP calls for downloader/uploader plugin negotiation and for direct file retrieval, with no restriction on target scheme, IP, or port (e.g., no blocking of loopback/link-local/internal ranges, akin to the missing `WEBHOOK_ALLOWED_SCHEMES`/`WEBHOOK_ADDITIONAL_BLOCKED_NETWORKS` protections Nautobot added): [5](#0-4) [6](#0-5) [7](#0-6) 

The fetch/validate cycle that drives these downloads is reachable purely through chain-observed mirror/subscription state, with no host allow-listing at any layer: [8](#0-7) 

### Impact Explanation
Any Chia user who is willing to spend a small on-chain fee to create a mirror coin for a `store_id` (their own or one they merely track) can direct every peer node running DataLayer with subscriptions to that store to issue HTTP requests to attacker-chosen hosts, including internal/loopback/link-local addresses or cloud-metadata endpoints, potentially exfiltrating internal service responses (SSRF), performing port scanning of the victim's internal network, or hitting internal management interfaces that trust localhost callers. Because the mirror mechanism is chain-anchored, the attack persists and is replayed by every future subscriber, not just a single victim.

### Likelihood Explanation
Likelihood is High: `add_mirror` is a normal wallet RPC action requiring only wallet funds and a `store_id`, no special permission is required beyond running a DataLayer-enabled wallet, and any node that later subscribes to (or already owns/tracks) that store id will automatically process the malicious URLs during its regular `periodically_manage_data()` / `update_subscription()` cycle without any operator intervention.

### Recommendation
Add server-side validation of mirror/plugin URLs before they are used to make outbound requests: restrict allowed schemes (e.g., http/https only), reject or gate requests to private/loopback/link-local/metadata IP ranges by default (with an explicit opt-in override, mirroring Nautobot's `WEBHOOK_ALLOWED_SCHEMES` / `WEBHOOK_ADDITIONAL_BLOCKED_NETWORKS` / `WEBHOOK_ALLOWED_HOSTS` design), and apply this validation both when mirror URLs are created (`add_mirror`) and when they are consumed from chain state (`update_subscriptions_from_wallet`, `get_downloader`, `get_uploaders`, `download_file`, `check_plugins`).

### Proof of Concept
1. Attacker creates (or already has) a DataLayer store and calls the `add_mirror` RPC with `urls=["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:6588/internal-admin"]`, paying the on-chain fee — no validation rejects these hosts (`chia/data_layer/data_layer.py:965-970`, `chia/wallet/wallet_rpc_api.py:3255-3274`).
2. The mirror coin is confirmed on-chain.
3. Any other node subscribed to (or owning) that `store_id` runs its periodic sync loop, calls `update_subscriptions_from_wallet`, pulls the URLs via `dl_get_mirrors`, and stores them as subscription server URLs (`chia/data_layer/data_layer.py:979-985`, `chia/data_layer/data_store.py:1668-1704`).
4. During `fetch_and_validate`/`download_file`/`get_downloader`/`get_uploaders`, the victim node's DataLayer service issues outbound HTTP requests to the attacker-chosen URL with no host/scheme restriction (`chia/data_layer/data_layer.py:642-694,731-747,1387-1403`; `chia/data_layer/download_data.py:146-168`), demonstrating SSRF against the victim's local/internal network.

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

**File:** chia/data_layer/download_data.py (L146-168)
```python

    log.info(f"Using downloader {downloader} for store {store_id.hex()}.")
    request_json = {
        "url": server_info.url,
        "client_folder": str(client_foldername),
        "filename": filename,
        "group_files_by_store": group_downloaded_files_by_store,
        "max_delta_file_size": max_delta_file_size,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                downloader.url + "/download",
                json=request_json,
                headers=downloader.headers,
                timeout=timeout,
            ) as response:
                res_json = await response.json()
                assert isinstance(res_json["downloaded"], bool)
                return res_json["downloaded"]
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        log.error(f"download_file could not get response from plugin {downloader}: {type(e).__name__}: {e}")
        return False
```
