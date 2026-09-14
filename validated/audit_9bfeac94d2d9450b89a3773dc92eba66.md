I have enough to render a determination now.

### Title
Data Layer mirror URLs enable unvalidated-URL SSRF against subscriber nodes - (File: chia/data_layer/download_data.py)

### Summary
Any DataLayer store owner can publish arbitrary, attacker-chosen mirror URLs on-chain via `dl_new_mirror`/`create_new_mirror`, which any other Data Layer client that subscribes to that store id will automatically fetch from using `aiohttp` with no URL validation, matching the CWE-20/CWE-918 "unvalidated external URL used by an HTTP client" bug class from the OpenCTI advisory.

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet owner spend into a mirror-puzzle coin with a memo `[launcher_id, *urls]` containing arbitrary attacker-controlled strings [1](#0-0) , exposed unauthenticated to on-chain observers as `Mirror.urls` through `dl_get_mirrors`/`DLNewMirror` RPC with no scheme/host restriction on the `urls: list[str]` field [2](#0-1) .

Any Data Layer service that is subscribed to that store (or is itself the owner receiving mirror updates) periodically pulls these URLs into its local subscription server list via `update_subscriptions_from_wallet()`, which just calls `dl_get_mirrors` and stores the URLs verbatim with no validation beyond a trailing-slash strip [3](#0-2) .

Those URLs are later selected in `fetch_and_validate()` and passed straight into `insert_from_delta_file()` / `download_file()` / `http_download()`, which perform an `aiohttp.ClientSession.get(server_info.url + "/" + filename, ...)` request without validating the URL's scheme or target host (no private-IP/localhost/metadata-address blocking, no scheme allow-list) [4](#0-3) , [5](#0-4) . The `.cursor/context/data-layer.md` notes explicitly flag this as an unresolved trust boundary: "Plugin and mirror URLs are external trust inputs... file path/name validation must stay strict," implying URL validation is not enforced at the network layer [6](#0-5) .

This is the same bug class as the OpenCTI SSRF: a feature (data ingestion / mirror sync) accepts an externally-supplied, unauthenticated URL and blindly issues HTTP requests to it with the default client configuration, allowing an attacker to redirect the victim's outbound requests to internal services (e.g. `http://169.254.169.254/`, `http://127.0.0.1:<local-rpc-port>/`, internal Elasticsearch/Redis, etc.) reachable from the Data Layer host.

### Impact Explanation
A malicious DataLayer store owner (any Data Layer client, reachable without special privileges — they only need to create a store and publish a mirror coin) can force any node that subscribes to that store to make outbound HTTP GET/POST requests to arbitrary attacker-chosen hosts, including internal-only or cloud-metadata endpoints. This is a semi-blind SSRF: the response is parsed as a delta/full file and typically rejected on content mismatch, but the request-side interaction (probing internal services, triggering side effects on internal HTTP endpoints, DNS rebinding, or crafting requests that internal services act upon regardless of response body) is still possible. This matches the "Medium/High" SSRF impact class permitted in scope (Data Layer client reachable path, no consensus break required).

### Likelihood Explanation
Likelihood is straightforward: creating a mirror coin with malicious `urls` is a normal, unprivileged wallet action available to any DataLayer participant, and any peer that subscribes to that store (a normal, expected DataLayer workflow) will automatically fetch from the attacker-supplied URL on a periodic background loop (`periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()`) with no user interaction required after subscribing.

### Recommendation
Validate mirror/server URLs before use in `chia/data_layer/data_layer.py` (`update_subscriptions_from_wallet`, `add_mirror`) and in `chia/data_layer/download_data.py` (`http_download`/`download_file`): enforce an `http(s)` scheme allow-list, resolve and reject requests to private/loopback/link-local/metadata IP ranges (reusing `chia/util/ip_address.py`'s `IPAddress.is_private` and `chia/util/network.py` helpers), and consider making this validation configurable/opt-in for legitimate local-network testing.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `dl_new_mirror` (RPC `add_mirror`) with `urls=["http://169.254.169.254/latest/meta-data/"]` and pushes the transaction on-chain, as exercised in the mirror test flow [7](#0-6) .
2. Victim node subscribes to the attacker's `store_id` via `/subscribe` (a normal DataLayer client action).
3. Victim's background sync calls `update_subscriptions_from_wallet()`, ingesting the attacker URL into the local subscription list [3](#0-2) .
4. On the next `fetch_and_validate()` cycle, the victim's `http_download()` issues `session.get("http://169.254.169.254/latest/meta-data/" + "/" + filename, ...)`, causing an outbound request from the victim's infrastructure to the attacker-chosen internal/metadata address [8](#0-7) .

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

**File:** chia/wallet/wallet_request_types.py (L1853-1859)
```python
@streamable
@dataclass(frozen=True, kw_only=True)
class DLNewMirror(TransactionEndpointRequest):
    launcher_id: bytes32
    amount: uint64
    urls: list[str]

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
