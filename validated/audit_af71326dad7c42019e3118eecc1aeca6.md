### Title
Blind SSRF via attacker-controlled DataLayer mirror/subscription URLs causing outbound requests from other users' DataLayer nodes - (File: chia/data_layer/data_layer.py)

### Summary
DataLayer store owners publish "mirror" URLs on-chain via `add_mirror`, and any DataLayer client can also add arbitrary subscription URLs via the `subscribe` RPC. Other DataLayer nodes that subscribe to (or own/track) a store automatically pull these URLs from wallet-tracked mirror coins and issue outbound HTTP requests to them with no validation of scheme, host, or destination (no SSRF protections such as blocking loopback/link-local/private ranges). This mirrors the GitLab blind-SSRF bug class (CVE-2022-4335), where user-supplied URLs let an attacker make the server connect to arbitrary/internal hosts.

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet owning a DataLayer singleton publish an arbitrary list of URL strings as memos on a mirror coin, with no URL validation [1](#0-0) . These mirror coins are globally visible on-chain and readable by any peer via `dl_get_mirrors`, and `DataLayer.update_subscriptions_from_wallet()` copies every mirror URL for a tracked/subscribed store straight into the local subscriptions table with only trivial `rstrip("/")` normalization — no scheme allow-list, no private/loopback IP filtering [2](#0-1) .

During the periodic sync loop, `update_subscription()` calls `update_subscriptions_from_wallet()` then `fetch_and_validate()` for every tracked subscription, including stores that are merely subscribed-to or "pseudo-subscribed" owned stores [3](#0-2) . `fetch_and_validate()` picks a server URL from `get_available_servers_for_store()` (populated from the unfiltered mirror/subscription URLs) and passes it into `insert_from_delta_file()` → `download_file()` [4](#0-3) .

`download_file()` in `chia/data_layer/download_data.py` performs an outbound HTTP GET/plugin POST directly to `server_info.url` (the attacker-supplied mirror URL) via `http_download()` when no downloader plugin handles the URL scheme, with no destination restriction [5](#0-4) . Any user can also directly control this via the public `subscribe` RPC, which accepts arbitrary `urls` and stores them without validation before the client's periodic loop begins connecting to them [6](#0-5) [7](#0-6) .

The project's own architecture notes acknowledge mirror/plugin URLs are untrusted inputs but state the only defense applied is validating *downloaded content* against the wallet root — not validating the *destination* being connected to [8](#0-7) .

### Impact Explanation
An unprivileged attacker who owns (or briefly creates) a DataLayer singleton can publish a mirror coin with a URL pointing at an internal/loopback address (e.g., a local RPC port, cloud metadata endpoint, or internal service) using `add_mirror`, or directly instruct another user's node to connect via the `subscribe` RPC. Any Data Layer client that subscribes to that store, or that is coerced/tricked into subscribing, will have its DataLayer service issue outbound HTTP requests to the attacker-chosen destination on a recurring basis (`periodically_manage_data`), and reflect status/log side channels (success/failure, timing) back through logs/RPC — a classic blind SSRF pattern. This can be used to probe internal network topology, hit local RPC/metadata services from the DataLayer host, or as an amplification/DoS vector against arbitrary hosts through many nodes replicating the same subscription.

### Likelihood Explanation
Reaching this requires only standard Data Layer usage: creating a store/mirror coin (a normal wallet action available to any user) or calling the public `subscribe` DataLayer RPC with attacker-chosen URLs. No special privileges, node compromise, or malicious peer/network position are required — this is squarely within "Data Layer client" reachable functionality named in scope. The periodic sync loop runs automatically without user awareness of the destination once a subscription exists.

### Recommendation
Validate destination URLs before making outbound connections in `DataLayer.subscribe()`, `update_subscriptions_from_wallet()`, and `download_data.http_download()`/`download_file()`: enforce an allow-listed URL scheme, resolve and reject requests to loopback/link-local/private/reserved IP ranges (unless explicitly configured for local testing), and consider requiring user confirmation before auto-following wallet-published mirror URLs, mirroring standard SSRF mitigations recommended for the analogous GitLab issue.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror` (or a victim's own `subscribe` RPC is invoked with attacker-supplied `urls`) with a URL such as `http://127.0.0.1:<internal-port>/` or `http://169.254.169.254/`.
2. A victim node subscribes to (or owns/tracks) that store; `update_subscriptions_from_wallet()` copies the URL into local subscriptions unfiltered [2](#0-1) .
3. On the next `periodically_manage_data()` cycle, `update_subscription()` → `fetch_and_validate()` → `insert_from_delta_file()` → `download_file()` issues an HTTP request to the attacker-controlled URL from the victim's DataLayer process [4](#0-3) , confirmable via response/timing differences or logs, demonstrating blind SSRF.

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

**File:** chia/data_layer/data_layer.py (L1102-1116)
```python
    async def update_subscription(
        self,
        worker_id: int,
        job: Job[Subscription],
    ) -> None:
        subscription = job.input

        try:
            await self.update_subscriptions_from_wallet(subscription.store_id)
            await self.fetch_and_validate(subscription.store_id)
            await self.upload_files(subscription.store_id)
            await self.clean_old_full_tree_files(subscription.store_id)
        except Exception as e:
            self.log.error(f"Exception while fetching data: {type(e)} {e} {traceback.format_exc()}.")

```

**File:** chia/data_layer/download_data.py (L126-145)
```python
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

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
