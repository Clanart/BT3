### Title
SSRF via unvalidated DataLayer mirror URLs enables outbound requests to internal endpoints - (File: `chia/data_layer/download_data.py`)

### Summary
DataLayer subscribers automatically fetch mirror server URLs that are published on-chain by store owners, with no validation of scheme, host, or destination (no blocking of loopback, link-local, or private-network addresses) before the local node issues an outbound HTTP GET to them.

### Finding Description
A DataLayer store owner publishes mirror URLs via the `add_mirror` RPC, which accepts an arbitrary list of strings with only a non-empty check (`"URL list can't be empty"`), as shown in tests using values like `http://127.0.0.1/8000` [1](#0-0) . These URLs are stored as `ServerInfo` records associated with a store id, and any DataLayer node that subscribes to that store id (a normal Data Layer client action) will periodically call `fetch_and_validate()`, which shuffles the list of `servers_info` and, for each one, calls `insert_from_delta_file()` / `download_file()` [2](#0-1) .

`download_file()` (when no plugin `downloader` is configured, which is the default path) invokes `http_download()`, which builds the request URL directly from the attacker-controlled `server_info.url` and issues an `aiohttp.ClientSession().get()` request against it, with no scheme allow-list and no destination-address filtering: [3](#0-2) . The only constraint applied elsewhere is on the downloaded *filename* format (`is_filename_valid`), not on the mirror host/URL itself, and the project's own module notes explicitly state that "Plugin and mirror URLs are external trust inputs," acknowledging they are not vetted for network-destination safety [4](#0-3) .

Because the URL is attacker-supplied and unfiltered, a malicious store owner can set mirror URLs pointing at internal-only services (e.g., `http://127.0.0.1:<local-rpc-port>/...`, cloud metadata endpoints, or other hosts on the operator's private network) that a victim's DataLayer node will reach out to as soon as it subscribes to and syncs that store — a fully automated, unprivileged-triggerable SSRF, matching CVE-2019-14255's underlying bug class (unauthenticated/unvalidated user-supplied URL fetched by a proxy-like fetcher against internal endpoints).

### Impact Explanation
This lets a remote, unprivileged DataLayer store owner cause victim nodes that subscribe to their store to make outbound HTTP requests to arbitrary internal or local addresses reachable from the victim host, potentially probing/interacting with local services (including the node's own RPC endpoints if unauthenticated, internal metadata services, or other LAN-only HTTP services) without the victim initiating the destination themselves. This is a real internal-network-reachability primitive triggered purely by subscribing to and syncing an attacker's public store — a normal Data Layer client action explicitly in scope.

### Likelihood Explanation
Likelihood is high for any node that subscribes to third-party DataLayer stores (a core, documented use case of DataLayer, e.g., following/mirroring public datasets). No special privilege beyond normal subscription/sync is needed by the attacker (publish a store + mirror urls) or the victim (subscribe + let the background sync loop run, which is automatic via `periodically_manage_data()`).

### Recommendation
Validate and restrict mirror/downloader URLs before they are stored (in `add_mirror`) and again before use in `http_download()`/`download_file()`: enforce an allow-listed scheme (http/https only), resolve and reject requests targeting loopback, link-local, private, and multicast address ranges (unless explicitly operator-opted-in for local testing), and consider requiring per-node explicit allow-listing of mirror hosts rather than trusting on-chain-published URLs implicitly.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `create_data_store`, then `add_mirror` with `urls=["http://127.0.0.1:<victim-local-service-port>/trigger", "http://169.254.169.254/latest/meta-data/"]` [1](#0-0) .
2. Victim node operator subscribes to the attacker's `store_id` for legitimate replication purposes.
3. Victim's `periodically_manage_data()` loop calls `update_subscription()` → `fetch_and_validate()`, which iterates `servers_info` (including the malicious mirror URLs) and calls `insert_from_delta_file()` → `download_file()` → `http_download()` [2](#0-1) [3](#0-2) .
4. The victim node issues an outbound GET request to the attacker-chosen internal/local URL, regardless of that address's sensitivity, demonstrating SSRF against internal endpoints reachable from the victim host.

### Citations

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2782-2783)
```python
        urls = ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        res = await data_rpc_api.add_mirror({"id": store_id.hex(), "urls": urls, "amount": 1, "fee": 1})
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

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
