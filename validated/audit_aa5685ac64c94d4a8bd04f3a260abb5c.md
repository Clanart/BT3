I have enough evidence to finalize the analysis. The DataLayer mirror-URL SSRF path is well-supported by the code I've already gathered: `add_mirror` (`chia/data_layer/data_layer.py:965-970`) publishes arbitrary URLs on-chain with only an emptiness check, `update_subscriptions_from_wallet` (`chia/data_layer/data_layer.py:979-985`) pulls those URLs verbatim into local subscription state, and `http_download`/`download_file` (`chia/data_layer/download_data.py:110-168,298-346`) perform unrestricted outbound `aiohttp` requests to those URLs with no host/CIDR validation, exactly mirroring the Gogs repository-migration SSRF bug class.

### Title
SSRF via unvalidated DataLayer mirror URLs fetched during subscription sync - (File: chia/data_layer/download_data.py)

### Summary
Chia's DataLayer lets any wallet holder publish "mirror" URLs on-chain for a store (`add_mirror`), and lets any other node "subscribe" to that store to replicate its data. When a local node subscribes to a store, it pulls the mirror URLs from wallet-visible chain state and later issues outbound HTTP requests to them with **no validation that the URL does not target internal/private network addresses**. This is the same bug class as GHSA-7v5r-r995-q2x2 (Gogs repository-migration SSRF): an attacker-controlled string, sourced from an untrusted party, is fetched server-side without any restriction on internal targets.

### Finding Description
- `DataLayer.add_mirror()` publishes a list of URLs on-chain for a store id, with the only check being that the list is non-empty: [1](#0-0) 
- Any node that subscribes to that store id (a normal, expected DataLayer operation, e.g. via `chia data subscribe`) periodically calls `update_subscriptions_from_wallet()`, which reads those mirror URLs verbatim from wallet RPC (`dl_get_mirrors`) and inserts them into the local subscriptions table without validating scheme, host, or target network range: [2](#0-1) [3](#0-2) 
- `get_available_servers_for_store()` simply returns these stored URLs, subject only to failure/backoff bookkeeping, not content validation: [4](#0-3) 
- `fetch_and_validate()` / `insert_from_delta_file()` then pick one of these URLs and use it to perform an outbound network request via `http_download()`, which builds the request URL directly from `server_info.url` and issues an `aiohttp` `GET`: [5](#0-4) [6](#0-5) 
- Nowhere in this chain (`add_mirror` → `update_subscriptions_from_wallet` → `get_available_servers_for_store` → `http_download`) is the URL checked against loopback/link-local/private CIDR ranges, non-HTTP(S) schemes, or a denylist of internal hosts — unlike Gogs, which after the fix rejects internal CIDR ranges as migration targets.

### Impact Explanation
Because store ids and their mirror URLs are attacker-controlled and become visible/subscribable by any other DataLayer participant, a malicious mirror publisher can cause any victim node that subscribes to their store to make outbound HTTP requests to arbitrary internal addresses (e.g. `http://127.0.0.1:<port>/...`, cloud metadata endpoints, or other hosts on the victim's private network) chosen entirely by the attacker at chain-publish time, not by the victim. This enables internal network reconnaissance/service discovery from the victim's vantage point and can be used to probe unauthenticated local services (e.g., other RPC ports) reachable only from that host — a classic SSRF impact, matching the CWE-918 classification and Medium severity of the referenced advisory. It does not directly cause coin/asset consequences, so it is scoped to the network reconnaissance/SSRF impact class rather than fund theft.

### Likelihood Explanation
Subscribing to third-party DataLayer stores to replicate their data is a documented, expected workflow (mirrors/subscriptions exist specifically to let unrelated nodes fetch each other's data). Any store owner can call `add_mirror` with URLs of their choosing at negligible cost (only a small on-chain fee), and any node that subscribes to that store will automatically attempt the fetch during its periodic `periodically_manage_data()` / `update_subscription()` cycle without further user interaction, making exploitation straightforward for anyone who can get a victim to subscribe (or for automated crawlers/indexers of DataLayer stores).

### Recommendation
Validate mirror/server URLs before storing or before dialing them: enforce an allow-listed scheme (`http`/`https`), resolve and reject requests targeting loopback, link-local, multicast, and RFC1918 private address ranges (unless explicitly opted in via configuration, e.g. for local test networks), and re-validate at the time of the actual `aiohttp` request (not just at subscription time) to defend against DNS rebinding. This should be applied both in `update_subscriptions_from_wallet()`/`DataStore.update_subscriptions_from_wallet()` (when ingesting mirror URLs) and in `http_download()` (as defense in depth immediately before dialing).

### Proof of Concept
1. Attacker creates a DataLayer store and runs `add_mirror` with `urls=["http://127.0.0.1:<internal-port>/", "http://169.254.169.254/latest/meta-data/"]` (or any other internal-only endpoint reachable from potential victims), as shown functioning in the existing test: [7](#0-6) 
2. Victim runs `chia data subscribe <attacker_store_id>` to replicate the attacker's public store (an ordinary, expected action).
3. The victim's DataLayer service periodically syncs subscription URLs from the wallet mirrors and populates local subscription state with the attacker-chosen URLs (`update_subscriptions_from_wallet`).
4. On the next `fetch_and_validate()` cycle, the victim's node issues an outbound `aiohttp` GET request built directly from the attacker-chosen URL via `http_download()`, hitting the internal/private target chosen by the attacker with no restriction.

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

**File:** chia/data_layer/data_store.py (L1832-1841)
```python
    async def get_available_servers_for_store(self, store_id: bytes32, timestamp: int) -> list[ServerInfo]:
        subscriptions = await self.get_subscriptions()
        subscription = next((subscription for subscription in subscriptions if subscription.store_id == store_id), None)
        if subscription is None:
            return []
        servers_info = []
        for server_info in subscription.servers_info:
            if timestamp > server_info.ignore_till:
                servers_info.append(server_info)
        return servers_info
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

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2767-2791)
```python
@pytest.mark.limit_consensus_modes(reason="does not depend on consensus rules")
@pytest.mark.anyio
async def test_mirrors(
    self_hostname: str, one_wallet_and_one_simulator_services: SimulatorsAndWalletsServices, tmp_path: Path
) -> None:
    wallet_rpc_api, full_node_api, wallet_rpc_port, ph, bt = await init_wallet_and_node(
        self_hostname, one_wallet_and_one_simulator_services
    )
    async with init_data_layer(wallet_rpc_port=wallet_rpc_port, bt=bt, db_path=tmp_path) as data_layer:
        data_rpc_api = DataLayerRpcApi(data_layer)
        res = await data_rpc_api.create_data_store({})
        assert res is not None
        store_id = bytes32.from_hexstr(res["id"])
        await farm_block_check_singleton(data_layer, full_node_api, ph, store_id, wallet=wallet_rpc_api.service)

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
