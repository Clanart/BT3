### Title
Server-Side Request Forgery via unauthenticated DataLayer mirror URLs - (File: `chia/data_layer/download_data.py`)

### Summary
Any user can publish an on-chain "mirror" coin for **any** DataLayer store (even one they do not own) containing arbitrary URLs in the coin's memos. Any other node that subscribes to (or tracks) that store id will automatically pull those URLs into its local subscription list and periodically issue outbound HTTP requests to them with no validation of scheme, host, or port. This lets a remote, unprivileged actor force victim nodes to make attacker-chosen HTTP requests, e.g. to internal services, cloud metadata endpoints, or arbitrary hosts/ports — a classic SSRF, analogous to the HertzBeat CVE-2024-56736 SSRF in OSS/API config handling.

### Finding Description
`DataLayerWallet.create_new_mirror()` creates a standard coin spend to the mirror puzzle hash whose memos are `[launcher_id, *urls]` — it does not verify that the caller owns or has any special relationship to `launcher_id`, and the `urls` are raw, attacker-controlled strings. [1](#0-0) 

The RPC surface (`dl_new_mirror`) exposes this directly to any local/remote RPC caller with wallet funds, with no additional authorization tied to store ownership: [2](#0-1) 

Any DataLayer service that is subscribed/tracking that `store_id` periodically calls `update_subscriptions_from_wallet()`, which reads all mirrors for the launcher id from the chain (`dl_get_mirrors`) and blindly merges their URLs into the local subscription table — again with no filtering of scheme/host: [3](#0-2) [4](#0-3) 

During the sync loop, `fetch_and_validate()` iterates these attacker-supplied server URLs and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which performs an `aiohttp` GET directly against `server_info.url` with no allow-list, no restriction on private/loopback/link-local addresses, and no scheme restriction: [5](#0-4) [6](#0-5) 

No component in this chain (`create_new_mirror`, `add_mirror`/`dl_new_mirror` RPC, `update_subscriptions_from_wallet`, `fetch_and_validate`, `http_download`) validates that the target host is a legitimate/expected DataLayer mirror endpoint (e.g., blocking `127.0.0.1`, `169.254.169.254`, RFC1918 ranges, non-http(s) schemes, or unexpected ports).

### Impact Explanation
Any unprivileged user who can submit a spend bundle (creating a small "mirror" coin) can cause every other DataLayer node that later subscribes to or tracks the targeted `store_id` to issue outbound HTTP requests to a URL of the attacker's choosing. This can be used to:
- Probe/attack internal network services and infrastructure reachable from victim nodes (SSRF pivot).
- Reach cloud instance metadata endpoints (e.g. `169.254.169.254`) on nodes running in cloud environments, potentially exfiltrating credentials via error/log side channels or response timing.
- Trigger unwanted requests against arbitrary internet hosts, using victim DataLayer nodes as a request proxy (abuse/DoS amplification against a third party).

This fits the "Data Layer roots and proofs" in-scope category and is reachable purely through a user-submitted spend bundle plus normal DataLayer client behavior (subscribing to a store), without any peer/farmer/operator privilege.

### Likelihood Explanation
Exploitation only requires the ability to spend a small amount of XCH to create a mirror coin for an arbitrary (even non-owned) `launcher_id`, and for a victim to be subscribed/tracking that store — DataLayer's periodic `periodically_manage_data()`/`update_subscription()` loop performs this automatically and continuously for all tracked stores, making the SSRF trigger largely automatic once a victim subscribes to an attacker-influenced store.

### Recommendation
- Validate mirror/subscription URLs before persisting or using them for outbound requests: restrict to `http`/`https` schemes, resolve and block private/loopback/link-local/metadata IP ranges, and optionally require operator opt-in/allow-listing of mirror hosts.
- Consider requiring store ownership or additional attestation before a mirror's URLs are trusted/merged into the local subscription table in `update_subscriptions_from_wallet()`.
- Apply the same validation in `add_mirror()`/`create_new_mirror()` at creation time so malicious URLs cannot even be published on-chain by DataLayer's own CLI/RPC.

### Proof of Concept
1. Attacker calls `dl_new_mirror` (or `data add_mirror` CLI) against an arbitrary `launcher_id` (does not need to own it), with `urls=["http://169.254.169.254/latest/meta-data/", "http://<internal-victim-service>:port/"]`, paying only a small fee/mirror amount. [2](#0-1) 
2. The mirror coin's spend bundle is broadcast and confirmed on chain like any other transaction.
3. A victim's DataLayer node, tracking/subscribed to that `launcher_id` (e.g., because it is following that data store), periodically runs `update_subscriptions_from_wallet()` and picks up the attacker's URLs into its local subscription table. [3](#0-2) 
4. On the next sync cycle, `fetch_and_validate()`/`http_download()` issues an outbound GET request from the victim node directly to the attacker-chosen URL/host/port. [6](#0-5)

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

**File:** chia/data_layer/data_store.py (L1668-1703)
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
