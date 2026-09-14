### Title
SSRF in DataLayer subscription/mirror URLs allows internal network probing - ([File: chia/data_layer/data_layer.py])

### Summary
The DataLayer service accepts arbitrary, user-supplied URLs (via `subscribe`, wallet-sourced mirror URLs, and CLI `-u/--url` options) and later dereferences them with outbound HTTP requests (`http_download`, `download_file`, `get_uploaders`, `get_plugin_info`) with no validation that the target host/IP is not an internal/private address. This mirrors the Gogs SSRF advisory (GHSA-q347-cg56-pcq4), where user-controlled migration URLs were not restricted from internal CIDRs.

### Finding Description
A local RPC caller can call the `subscribe` DataLayer RPC with an arbitrary URL list (`chia/cmds/data.py:332-353`, wired through to `DataStore.subscribe()` at [1](#0-0) ). These URLs are stored verbatim, with no scheme/host/CIDR restriction (no `ip_address`, `is_private`, or CIDR-blocking logic exists anywhere in `chia/data_layer/`).

The background sync loop (`periodically_manage_data` → `update_subscription` → `fetch_and_validate`) later selects one of these stored server URLs and issues real network requests to it via `insert_from_delta_file` → `download_file` → `http_download`: [2](#0-1) [3](#0-2) 

Additionally, mirror URLs pulled from the wallet (`dl_get_mirrors`, itself populated by `add_mirror`, which also takes arbitrary URLs from the caller) are merged into subscription URLs without validation: [4](#0-3) 

Because the request target (host, port, path) is fully attacker/user controlled, and the request is issued by the DataLayer server process (which may run on an internal network segment, in a container, or cloud VM), this can be used to probe internal-only endpoints (e.g., cloud metadata services, internal admin ports, or other services bound to loopback/internal interfaces) and observe response timing/HTTP status differences leaked back through logs or subscription "banned"/`num_consecutive_failures` state (queryable via `get_subscriptions`).

### Impact Explanation
This is a Server-Side Request Forgery: an unprivileged or semi-trusted local Data Layer client (anyone with access to the Data Layer RPC, e.g., a local automation script, a compromised low-privilege service, or a multi-tenant node operator scenario) can direct the node's outbound HTTP client to arbitrary internal or cloud-metadata addresses, enabling internal network/service discovery and potential exploitation of unauthenticated internal endpoints (e.g., cloud instance metadata credential theft). This matches the CWE-918 / GHSA-q347-cg56-pcq4 bug class exactly — the same root cause (accepting an arbitrary target URL for a background fetch operation with no internal-CIDR blocklist).

### Likelihood Explanation
Likelihood is moderate: exploitation requires access to the DataLayer RPC (local RPC caller), which is a permitted actor per this scan's scope ("Data Layer client" reachable actor). No additional privilege beyond calling `subscribe`/`add_mirror` is required, and the vulnerable code path (`fetch_and_validate`/`http_download`) runs automatically in the background loop without further confirmation.

### Recommendation
- Validate and resolve subscription/mirror URLs before storing/using them; reject loopback, link-local, private (RFC1918), and cloud-metadata address ranges (e.g., 169.254.169.254) unless explicitly allowed via configuration.
- Apply the same validation at both `DataStore.subscribe()` and `DataLayer.add_mirror()`/`update_subscriptions_from_wallet()` entry points, since mirror URLs bypass the CLI subscribe path.
- Consider re-validating resolved IPs at connection time (DNS rebinding protection) in `http_download`/`download_file`, not just at URL string validation time.

### Proof of Concept
1. Start a local DataLayer service and node with RPC access.
2. Call the `subscribe` RPC/CLI for a store id with `-u http://169.254.169.254/latest/meta-data/` (or an internal service URL such as `http://127.0.0.1:<internal-port>/`):
   `chia data subscribe -i <store_id> -u http://169.254.169.254/latest/meta-data/`
3. Wait for the periodic `update_subscription`/`fetch_and_validate` cycle; the node issues an outbound `session.get(url + "/" + filename, ...)` request (`chia/data_layer/download_data.py:311-319`) to the attacker-chosen host.
4. Observe via logs (`log.info(f"Downloading files {store_id}...Server used: {url}")` in `chia/data_layer/data_layer.py:661-666`) or via `get_subscriptions`/`num_consecutive_failures`/`ignore_till` state whether the internal target responded, was reachable, or timed out — leaking information about internal network topology/services.

### Citations

**File:** chia/data_layer/data_store.py (L1705-1739)
```python
    async def subscribe(self, subscription: Subscription) -> None:
        async with self.db_wrapper.writer() as writer:
            # Add a fake subscription, so we always have the store_id, even with no URLs.
            await writer.execute(
                "INSERT INTO subscriptions(tree_id, url, ignore_till, num_consecutive_failures, from_wallet) "
                "VALUES (:tree_id, NULL, NULL, NULL, 0)",
                {
                    "tree_id": subscription.store_id,
                },
            )
            all_subscriptions = await self.get_subscriptions()
            old_subscription = next(
                (
                    old_subscription
                    for old_subscription in all_subscriptions
                    if old_subscription.store_id == subscription.store_id
                ),
                None,
            )
            old_urls = set()
            if old_subscription is not None:
                old_urls = {server_info.url for server_info in old_subscription.servers_info}
            new_servers = [server_info for server_info in subscription.servers_info if server_info.url not in old_urls]
            for server_info in new_servers:
                await writer.execute(
                    "INSERT INTO subscriptions(tree_id, url, ignore_till, num_consecutive_failures, from_wallet) "
                    "VALUES (:tree_id, :url, :ignore_till, :num_consecutive_failures, 0)",
                    {
                        "tree_id": subscription.store_id,
                        "url": server_info.url,
                        "ignore_till": server_info.ignore_till,
                        "num_consecutive_failures": server_info.num_consecutive_failures,
                    },
                )

```

**File:** chia/data_layer/data_layer.py (L642-666)
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
```

**File:** chia/data_layer/data_layer.py (L965-985)
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
