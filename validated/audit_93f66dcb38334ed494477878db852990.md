### Title
Blind SSRF via unrestricted mirror URL fetch in DataLayer sync - ([File: chia/data_layer/download_data.py])

### Summary
DataLayer lets a store owner publish arbitrary mirror URLs on-chain (`add_mirror`), which any node subscribed to that store will automatically fetch from during sync, with no validation of scheme, host, or destination IP. This mirrors the GitLab CVE-2022-1188 bug class: a feature meant to fetch remote data from a user-supplied URL becomes a blind SSRF primitive against the fetching node's network.

### Finding Description
A DataLayer store owner calls `DataLayer.add_mirror()`, which forwards attacker-controlled `urls` straight to the wallet RPC with no validation: [1](#0-0) 

These mirror URLs are published on-chain as coin memos and later read back by any node tracking/subscribing to that store via `update_subscriptions_from_wallet()`, which pulls the URLs from `dl_get_mirrors` and stores them as subscription servers without any allowlist or scheme/host restriction: [2](#0-1) 

During the periodic sync loop, `fetch_and_validate()` iterates these attacker-supplied server URLs and issues outbound HTTP requests to them via `insert_from_delta_file()` → `download_file()` → `http_download()`: [3](#0-2) [4](#0-3) 

`http_download()` performs `session.get(server_info.url + "/" + filename, ...)` with the URL taken verbatim from the on-chain mirror record — no check against loopback/link-local/private IP ranges (e.g. `127.0.0.1`, `169.254.169.254`, internal hostnames), and no restriction to `http(s)` only. The only server-side validation tests use plain loopback URLs (`http://127.0.0.1/8000`), confirming no filtering is applied anywhere in this path: [5](#0-4) 

The project's own module notes explicitly acknowledge mirror/plugin URLs are "external trust inputs" but only discuss data-integrity trust (root-hash verification of downloaded content), not network-destination trust: [6](#0-5) 

Because the fetch is triggered automatically by the background subscription loop (`periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()`), any wallet user who can create/own a DataLayer store and add a mirror can cause every other node that subscribes to that store to issue attacker-directed outbound requests — a classic blind SSRF: the victim node's own network position is used to probe/interact with internal-only endpoints (cloud metadata services, internal admin ports, other local RPC ports) chosen by the attacker.

### Impact Explanation
This is a Medium-severity SSRF: it does not by itself corrupt consensus, forge coins, or bypass authorization, but it lets a remote, unprivileged store owner force victim DataLayer nodes to make arbitrary outbound HTTP(S)/network requests to attacker-chosen destinations (including internal/loopback/cloud-metadata addresses), matching the "blind SSRF through mirroring" bug class in the referenced CVE. Exploitation can be used for internal network reconnaissance, hitting internal services/ports that are normally unreachable from the internet, or abusing IMDS-style endpoints on cloud-hosted nodes. Actual data downloaded is still hash-validated (`insert_from_delta_file`), limiting content exfiltration, but the request itself (timing, side effects, port-scanning) is fully attacker-controlled.

### Likelihood Explanation
Likelihood is high for triggering: creating a store, adding a mirror URL, and having any peer subscribe/auto-subscribe to that store is a normal, unprivileged DataLayer workflow requiring no special permission — the sync loop fetches automatically once a subscription exists, with no manual approval step for URL destinations.

### Recommendation
Validate and restrict mirror/server URLs before storing/subscribing and before dispatching HTTP requests in `http_download()`/`download_file()`: enforce an allowlist of schemes (`http`/`https` only), resolve and reject loopback/link-local/private/multicast/metadata IP ranges, and consider requiring operator opt-in or a maintained allowlist for mirror hosts. Apply the same checks in `update_subscriptions_from_wallet()`/`data_store.subscribe()` so malicious on-chain mirror URLs are filtered before being persisted as subscription servers.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror` with a URL pointing at an internal/loopback address reachable from victim nodes, e.g. `http://169.254.169.254` or `http://127.0.0.1:<internal-port>` — accepted with no validation per `DataLayer.add_mirror()`.
2. Victim node subscribes/tracks the attacker's store (e.g., to follow public data); `update_subscriptions_from_wallet()` pulls the malicious mirror URL into its local subscription table.
3. On the next `periodically_manage_data()` cycle, `fetch_and_validate()` selects that server and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, causing the victim node to issue `GET <attacker-url>/<filename>` from its own network context, at attacker-chosen timing and target.

### Citations

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

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2782-2791)
```python
        urls = ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        res = await data_rpc_api.add_mirror({"id": store_id.hex(), "urls": urls, "amount": 1, "fee": 1})

        await farm_block_check_singleton(data_layer, full_node_api, ph, store_id, wallet=wallet_rpc_api.service)
        mirrors = await data_rpc_api.get_mirrors({"id": store_id.hex()})
        mirror_list = mirrors["mirrors"]
        assert len(mirror_list) == 1
        mirror = mirror_list[0]
        assert mirror["urls"] == ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        coin_id = mirror["coin_id"]
```

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
