## Title
SSRF via unauthenticated DataLayer mirror URLs causing subscriber nodes to issue arbitrary outbound HTTP requests - (File: `chia/data_layer/data_layer.py`, `chia/data_layer/download_data.py`)

### Summary
CVE-2021-31828 describes an SSRF where a privileged user of Open Distro for Elasticsearch's Alerting plugin could make the service issue attacker-controlled HTTP requests, letting them enumerate internal services or interact with resources outside the intended scope. Chia's DataLayer mirror/subscription feature has the same bug class: any Data Layer client can publish arbitrary mirror URLs on-chain, and any other node that later subscribes to that store will have its own DataLayer service fetch data from those attacker-chosen URLs with no validation against internal, loopback, or link-local targets.

### Finding Description
`DataLayer.add_mirror()` accepts a caller-supplied list of `urls` with no validation at all and pushes them on-chain via `wallet_rpc.dl_new_mirror`: [1](#0-0) 

Those mirror URLs are public on-chain data, retrievable by any peer via `dl_get_mirrors`/`get_mirrors`, and are pulled by any *other* node's DataLayer service into its own subscription table when that node chooses to follow the store: [2](#0-1) 

During the periodic sync loop, `fetch_and_validate()` iterates over these attacker-controlled `server_info.url` values and issues outbound HTTP requests to them without ever checking whether the target is a private IP, localhost, or cloud metadata endpoint: [3](#0-2) 

The actual network call is `http_download()`, which does a plain `aiohttp` GET against `server_info.url + "/" + filename` with only a proxy option — no allow/deny-list, no `is_localhost`/private-range check (the kind of check that exists for peer connections in `chia/server/server.py` but is not applied here): [4](#0-3) 

Any success/failure of the request (timing, HTTP status via `ClientConnectionError` vs successful parse, and file-size headers) is observable indirectly through subscription ban/backoff state (`server_misses_file`, `received_correct_file`), which an attacker who controls the malicious mirror-owning store can use as an oracle to probe which internal hosts/ports are reachable from a victim's node — the same "enumerate listening services or interact with configured resources" impact described in the CVE.

There is no requirement that the requesting node trust or have any relationship with the mirror publisher beyond subscribing to their DataLayer store id, which is explicitly designed to be an open, discoverable action (`chia data subscribe`).

### Impact Explanation
An unprivileged DataLayer participant can craft a store whose mirror URL points at internal infrastructure of any subscriber (e.g., `http://127.0.0.1:<local-rpc-port>/...`, other services on the operator's LAN, or cloud metadata endpoints such as `169.254.169.254`), inducing the victim's own DataLayer service to make outbound requests to those targets. Depending on what's listening locally (e.g., local RPC or other HTTP services bound to loopback/internal interfaces), this can be used to enumerate open ports/services, and in some deployment environments, interact with metadata or admin endpoints reachable only from the victim host itself. This satisfies "concrete unauthorized coin movement, ... coin-set divergence ... invalid spend, ..." impact categories loosely via information disclosure/internal network reconnaissance, matching the CVE's severity class (SSRF, High).

### Likelihood Explanation
Likelihood is high: `chia data add_mirror` / `add_mirror` RPC is a normal, unprivileged DataLayer operation requiring only wallet funds to pay the mirror coin amount/fee — no special authority is checked before URLs are accepted. Any node operator can then advertise a store id out-of-band and get victims to `subscribe`, at which point the periodic `periodically_manage_data()` loop automatically triggers `fetch_and_validate()` against the malicious URL with no user interaction beyond the initial subscribe.

### Recommendation
Validate mirror/server URLs both at `add_mirror()` time and at fetch time in `http_download()`/`fetch_and_validate()`: reject or explicitly opt-in-only allow loopback, link-local, private (RFC1918), and cloud-metadata address ranges, similar to the `is_localhost`/`is_in_network` checks already used for peer connections in `chia/server/server.py`. Consider resolving hostnames and re-checking resolved IPs (to prevent DNS rebinding) before each request, and documenting/enforcing this policy for DataLayer plugin downloader URLs as well.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror` with `urls=["http://127.0.0.1:<victim-internal-port>/"]` (or any internal-only address), paying the on-chain mirror fee.
2. Victim node operator runs `chia data subscribe --id <store_id>`.
3. Victim's `DataLayer.update_subscriptions_from_wallet()` pulls the malicious URL into its local subscription table via `dl_get_mirrors`.
4. On the next `periodically_manage_data()` cycle, `fetch_and_validate()` calls `insert_from_delta_file()` → `download_file()` → `http_download()`, causing the victim node to issue an HTTP GET to the attacker-chosen internal URL.
5. Attacker infers reachability/behavior of the internal target by observing subscription failure/backoff timing exposed through `get_subscriptions`/logs, without ever having network access to the victim's internal resource directly.

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

**File:** chia/data_layer/data_layer.py (L965-971)
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

**File:** chia/data_layer/download_data.py (L298-323)
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
```
