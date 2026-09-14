### Title
SSRF via unvalidated Data Layer mirror URLs causes any subscribing node to send outbound requests to attacker-chosen hosts/schemes - (File: `chia/data_layer/download_data.py`)

### Summary
Chia's Data Layer lets any wallet owner of a DataLayer singleton (store) publish a list of arbitrary "mirror" URLs on-chain via the `dl_new_mirror` transaction endpoint. These URLs are later read by every other node that subscribes to that store and are passed, unvalidated, directly into an outbound `aiohttp` request built from string concatenation. This is the same bug class as CVE-2023-46236 (FOG SSRF): an unprivileged, remote-influenced value is used verbatim as the target of a server-initiated HTTP request, with no scheme/host allow-list, enabling SSRF against the fetching node's internal network.

### Finding Description
Mirror URLs are supplied by any user who owns a DL singleton (an unprivileged wallet action) through `add_mirror`/`dl_new_mirror`, which stores the raw string list on-chain as part of a `Mirror` coin (`urls` field, e.g. `["foo", "bar"]` per the RPC and `data_layer_wallet.py` mirror puzzle handling). Any other node that subscribes to the same store id will later synchronize mirror URLs from wallet state via `DataLayer.update_subscriptions_from_wallet()`: [1](#0-0) 

These URLs become `ServerInfo.url` values consumed by `get_available_servers_for_store()` and are then used, with no scheme or host validation, directly as the target of outbound HTTP GET requests in `http_download()`: [2](#0-1) 

The URL is simply string-concatenated (`server_info.url + "/" + filename`) and passed to `aiohttp.ClientSession().get(...)`. There is no restriction preventing the URL from pointing at `http://127.0.0.1:<internal-rpc-port>/...`, a cloud metadata endpoint (`http://169.254.169.254/...`), or other internal-only services reachable from the DataLayer node's network position. The same unvalidated URL is also forwarded to any configured downloader plugin in `download_file()`: [3](#0-2) 

and to `get_downloader()`'s plugin discovery POST: [4](#0-3) 

The whole flow is invoked automatically by the background sync loop (`fetch_and_validate()` / `periodically_manage_data()`), which iterates over all `servers_info` for a store and performs the download without any operator interaction: [5](#0-4) 

The publishing side (`add_mirror`) performs no URL validation, only forwarding the caller-supplied `urls` list to the wallet action: [6](#0-5) [7](#0-6) 

The `.cursor` architecture notes for this subsystem explicitly acknowledge that "Plugin and mirror URLs are external trust inputs," confirming this is a known-risky boundary rather than an incidental oversight: [8](#0-7) 

### Impact Explanation
Any wallet user can create a DataLayer store, publish a `dl_new_mirror` transaction with attacker-chosen URLs (e.g., pointing at localhost RPC ports, cloud metadata services, or internal-only hosts on the victim's network), and wait for other DataLayer nodes to subscribe to that store (or convince a specific victim to subscribe, e.g. via an offer that references the store). When those nodes run their periodic sync (`update_subscription()` → `fetch_and_validate()` → `insert_from_delta_file()` → `download_file()`/`http_download()`), the victim's DataLayer service will issue outbound HTTP requests to the attacker-chosen URL. This can be used to probe/interact with internal services not otherwise reachable from the public network, exfiltrate response data indirectly through error/timing behavior, or interact with local admin surfaces reachable only from localhost, depending on network topology. This matches the sanctioned impact class of "coin-set divergence" adjacent SSRF analogs is not directly applicable, but the requirement is "spend-triggered ... processing halt" or similar unauthorized action; here the closer match is direct SSRF against the node itself, mirroring the FOG report's core bug class (unauthenticated/unprivileged-triggered arbitrary outbound request).

### Likelihood Explanation
Likelihood is high for triggering the outbound request (any wallet holder can create a store and mirror with zero special permission, and mirror sync runs automatically and periodically for any subscribed store), but the severity of resulting internal exposure depends heavily on the deployment's local network trust boundaries (e.g., whether an internal RPC or cloud metadata endpoint is actually reachable from the machine running the DataLayer service). There is no code-level barrier (scheme allow-list, private-IP blocklist) preventing the attempt itself.

### Recommendation
Validate and restrict mirror/downloader URLs before use: enforce an allow-list of schemes (e.g., `http`/`https`/`s3` only where expected), reject requests to loopback/link-local/private address ranges unless explicitly configured (similar to typical SSRF mitigations), and apply this validation both when a URL is accepted from wallet state (`update_subscriptions_from_wallet`) and immediately before constructing any outbound request in `http_download()`, `download_file()`, and `get_downloader()`.

### Proof of Concept
1. Attacker creates a DataLayer store via `create_data_store` and calls `add_mirror`/`dl_new_mirror` with `urls=["http://127.0.0.1:8555/some_internal_admin_route"]` (or an internal-network/cloud-metadata address), a normal unprivileged wallet action [6](#0-5) .
2. Victim node subscribes to the store id (e.g., via `dl_track_new`/offer flow) and its background sync loop periodically calls `update_subscriptions_from_wallet()`, pulling in the attacker's URL as a `ServerInfo` [1](#0-0) .
3. On the next `fetch_and_validate()` cycle, the victim node performs `http_download()` against the attacker-controlled URL with no validation [2](#0-1) , causing the victim's server to issue a request to the attacker-chosen host/path.

### Citations

**File:** chia/data_layer/data_layer.py (L642-667)
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

**File:** chia/data_layer/download_data.py (L147-168)
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

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
