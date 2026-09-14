I have enough evidence to write the finding now.

### Title
Server-Side Request Forgery in DataLayer mirror/subscription fetch — unvalidated mirror URLs trigger outbound requests from the DataLayer node - ([File: chia/data_layer/download_data.py])

### Summary
DataLayer lets any store owner publish arbitrary URLs on-chain as "mirrors" for their store via the `add_mirror` RPC, and lets any DataLayer node auto-subscribe to and fetch from mirrors advertised by *any* store it tracks. The URL is later used verbatim by the node's background sync loop to issue an outbound HTTP GET, with no validation of scheme, host, or destination network (loopback/link-local/internal ranges), producing a classic SSRF reachable by any wallet user/DataLayer participant.

### Finding Description
`DataLayer.add_mirror()` accepts an arbitrary list of `urls` from the caller and pushes them on-chain via wallet RPC `dl_new_mirror` with no URL/host validation: [1](#0-0) 

Any peer that subscribes to that store id will later pull these mirror URLs from wallet state into its local subscription table: [2](#0-1) 

The periodic sync loop (`periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()`) then iterates the stored server URLs and calls `insert_from_delta_file()` / `download_file()` / `http_download()`, which performs a direct `aiohttp` GET to `server_info.url + "/" + filename` with only `proxy_url`/timeout/size controls — no scheme or destination filtering: [3](#0-2) [4](#0-3) 

Nothing in the mirror-URL path (`add_mirror`, `update_subscriptions_from_wallet`, `get_available_servers_for_store`, `http_download`) restricts the URL to a public host, blocks loopback/RFC1918/link-local addresses (e.g. `169.254.169.254` cloud metadata, `127.0.0.1:<internal-rpc-port>`), or enforces `https`. The module's own documentation acknowledges mirror URLs are "external trust inputs," but that note only addresses data-integrity trust (root verification after download), not the SSRF exposure of the outbound request itself: [5](#0-4) 

Only the S3 plugin path enforces a scheme check (`s3://`), confirming that no equivalent restriction exists for the default HTTP path: [6](#0-5) 

### Impact Explanation
A DataLayer node automatically and periodically issues outbound HTTP requests to attacker-chosen URLs sourced from on-chain mirror data of any subscribed store — including stores created by unprivileged/anonymous singleton owners. This can be used to probe/interact with internal-network services or cloud metadata endpoints reachable from the host running the DataLayer service (e.g., other local admin RPC ports, internal infra), and to trigger repeated automated requests as part of the normal sync loop without any user awareness, since subscription and mirror discovery run in a background task (`periodically_manage_data`) rather than requiring explicit user action per request. This meets the "Data Layer client" reachable-actor criteria and yields concrete network-reachable SSRF impact from a single on-chain mirror publication.

### Likelihood Explanation
High. `add_mirror` is a normal, unauthenticated-from-the-chain's-perspective DataLayer operation available to any singleton owner, requires no elevated privilege, and subscription/mirror sync is a fully automatic background loop that any node participating in DataLayer for a given store id will run.

### Recommendation
Validate mirror/server URLs before persisting or dereferencing them: restrict scheme to `http(s)`, resolve and reject loopback/link-local/private/multicast/metadata address ranges (defense against DNS rebinding too), and consider requiring operator opt-in/allowlisting for outbound mirror fetch destinations in `chia/data_layer/data_layer.py::add_mirror`/`update_subscriptions_from_wallet` and `chia/data_layer/download_data.py::http_download`/`download_file`.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror` (`chia/data_layer/data_layer.py` `add_mirror()`) with `urls=["http://169.254.169.254/latest/meta-data/"]` (or an internal service URL), publishing this on-chain via `dl_new_mirror`.
2. A victim DataLayer node subscribes to (or is configured to track) the attacker's store id, causing `update_subscriptions_from_wallet()` to pull the malicious URL into its local subscription table (`chia/data_layer/data_layer.py:979-985`).
3. On the next `periodically_manage_data()` cycle, `fetch_and_validate()` selects that server and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which issues `aiohttp` GET requests to the attacker-controlled URL from the victim node's network context (`chia/data_layer/download_data.py:298-323`), with no host/scheme validation performed anywhere along the path.

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

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```

**File:** chia/data_layer/s3_plugin_service.py (L250-253)
```python
        parse_result = urlparse(data["url"])
        for store in self.stores:
            if store.id == store_id and parse_result.scheme == "s3" and data["url"] in store.urls:
                return web.json_response({"handle_download": True, "urls": list(store.urls)})
```
