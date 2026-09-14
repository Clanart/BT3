## Title
DataLayer mirror URLs are fetched by subscriber nodes without SSRF/host validation - (File: `chia/data_layer/download_data.py`)

### Summary
Chia's DataLayer lets any wallet owning a DL singleton publish arbitrary "mirror" URLs on-chain via `add_mirror`, and lets any peer node "subscribe" to a store id and periodically pull data from those URLs. Neither the on-chain publication path nor the client-side fetch path validates the URL's scheme or target host, so a malicious store owner can point mirrors at internal/loopback/link-local addresses and have every subscribing node's `DataLayer` service issue outbound HTTP requests to them — a classic SSRF pattern, closely analogous to CVE-2026-17617 (SSRF from unvalidated URLs in custom resources).

### Finding Description
`DataLayer.add_mirror()` only checks that the URL list is non-empty before asking the wallet to publish the URLs on-chain: [1](#0-0) 

The RPC endpoint passes the raw, attacker-controlled string straight through with no scheme/host allow-list: [2](#0-1) 

Any node that subscribes to that store pulls the mirror list from the wallet and stores it as-is: [3](#0-2) 

Later, `fetch_and_validate()` iterates these server URLs and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which performs an unrestricted `aiohttp` GET against `server_info.url` with no destination filtering (no block on loopback, link-local/metadata, or private RFC1918 ranges): [4](#0-3) [5](#0-4) 

Existing tests even set up mirrors pointing at `http://127.0.0.1/...` without any rejection, confirming there is no host validation in this path: [6](#0-5) 

### Impact Explanation
Because the mirror URL is chosen entirely by the store owner and consumed automatically by every subscriber's periodic sync loop (`periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()`), an attacker who publishes a store and gets other users/services (including automated indexer or wallet operators) to subscribe can force those nodes to issue authenticated-looking outbound GET requests to arbitrary hosts — e.g., cloud metadata endpoints, internal-only management ports, or other services reachable from the DataLayer host's network — with attacker-controlled path/filename components appended. This is a genuine SSRF vector reachable purely through a Data Layer client action (subscribing to an attacker's store id), matching the CVE's bug class (URL validation missing in custom-resource fetch paths).

### Likelihood Explanation
Likelihood is moderate-to-high: any user can create a DataLayer store, publish mirror URLs on-chain via a standard `add_mirror` transaction (no special privilege), and wait for other participants to subscribe to that store id (a normal, encouraged DataLayer workflow for replicating public datasets). No signature bypass or consensus-level trick is required — only that a victim node subscribes to the malicious store.

### Recommendation
Validate and restrict mirror/server URLs before persisting or fetching them: enforce an allow-list of schemes (`http`/`https`), resolve and reject requests to loopback, link-local, and private address ranges (unless explicitly opted into for local testing), and consider making this validation configurable at both `add_mirror` (publish time) and `http_download`/`download_file` (fetch time) so already-published malicious mirrors can still be filtered client-side.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror` with `urls=["http://169.254.169.254/latest/meta-data/","http://localhost:<internal-admin-port>/"]` — accepted without validation per `DataLayer.add_mirror()`.
2. Victim node runs `chia data subscribe -id <store_id>` (or auto-discovers via `update_subscriptions_from_wallet`), pulling the attacker's mirror list.
3. Victim's `periodically_manage_data()` loop calls `fetch_and_validate()` → `http_download()`, issuing an outbound GET to the attacker-chosen host/path with no destination checks, as shown in `chia/data_layer/download_data.py:298-318`.

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

**File:** chia/data_layer/download_data.py (L298-318)
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
```

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2782-2790)
```python
        urls = ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        res = await data_rpc_api.add_mirror({"id": store_id.hex(), "urls": urls, "amount": 1, "fee": 1})

        await farm_block_check_singleton(data_layer, full_node_api, ph, store_id, wallet=wallet_rpc_api.service)
        mirrors = await data_rpc_api.get_mirrors({"id": store_id.hex()})
        mirror_list = mirrors["mirrors"]
        assert len(mirror_list) == 1
        mirror = mirror_list[0]
        assert mirror["urls"] == ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
```
