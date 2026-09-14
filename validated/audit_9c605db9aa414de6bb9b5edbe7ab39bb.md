### Title
DataLayer mirror URLs are fetched via unauthenticated outbound HTTP requests with no destination validation, enabling SSRF against internal network resources - (File: chia/data_layer/download_data.py)

### Summary
Any wallet holding a DataLayer wallet can publish arbitrary attacker-chosen URL strings on-chain as "mirror" server addresses for a DataLayer store via `dl_new_mirror`/`add_mirror`. Every node subscribed to that store id (which any Data Layer client can do freely) will later fetch data from those exact URLs over plain `aiohttp` with no scheme, host, or destination validation — an SSRF pattern directly analogous to the Paperclip `UriAdapter` SSRF in CVE-2017-0889, where a user-supplied URI is handed straight to an HTTP client that fetches from it.

### Finding Description
`DataLayerRpcApi.add_mirror` accepts a caller-supplied list of `urls` and forwards them unmodified to `DataLayer.add_mirror`, which calls wallet RPC `dl_new_mirror`: [1](#0-0) [2](#0-1) 

On the wallet side, `dl_new_mirror` only encodes the URL strings (`Mirror.encode_urls`) and publishes them as an on-chain mirror coin/message — there is no scheme allow-list, no rejection of loopback/link-local/private IP ranges, and no host validation: [3](#0-2) 

These published URLs later become the authoritative "server list" that other DataLayer nodes trust when syncing a subscribed store. `DataLayer.update_subscriptions_from_wallet` pulls the raw mirror URLs straight from wallet state into local subscriptions with no filtering: [4](#0-3) 

`DataLayer.fetch_and_validate` then iterates these attacker-controlled server URLs and performs outbound `aiohttp` requests to them (either directly or through a plugin), again with no host validation: [5](#0-4) 

The actual HTTP fetch is done in `http_download`, which issues a plain `aiohttp` GET to `server_info.url + "/" + filename` — `server_info.url` is exactly the attacker-supplied mirror URL, with no restriction preventing it from pointing at `127.0.0.1`, cloud metadata endpoints, or other internal-network addresses reachable from the node running DataLayer: [6](#0-5) 

The project's own test suite demonstrates that arbitrary loopback URLs (`http://127.0.0.1/8000`) are accepted as mirror URLs and stored/queried without any rejection, confirming there is no destination validation anywhere in this path: [7](#0-6) 

This mirrors the Paperclip `UriAdapter` bug class: a user/attacker-controlled URI string is passed unchecked into an HTTP fetch performed by the server/service, letting the attacker make the victim service issue requests to internal-only endpoints and potentially leak internal network information (response timing/errors, port-scan behavior, or content if the internal service echoes data back through error/logging paths).

### Impact Explanation
Any DataLayer node that subscribes to (or owns/pseudo-subscribes to) a store whose mirror list was poisoned by a malicious `add_mirror` publisher will have its DataLayer service (which typically runs on the same host/network as the full node, wallet, and other local RPC services) issue outbound HTTP requests to attacker-chosen destinations. This is a genuine SSRF: it can be used to probe/exfiltrate information about internal network resources (other local services, internal ports, cloud metadata endpoints) reachable from the machine running `chia data_layer`, matching the CVE's "attackers may be able to access information about internal network resources" impact statement. It does not directly cause coin-set divergence or fund theft, but it is a reachable, unauthenticated internal-network reconnaissance/SSRF primitive triggerable purely by an on-chain mirror publication that any wallet can make.

### Likelihood Explanation
Likelihood is high for any operator who runs the optional DataLayer service and subscribes to third-party stores (a normal usage pattern for DataLayer, e.g. following someone else's public dataset). Publishing a malicious mirror URL requires only a DataLayer wallet and a small on-chain fee — no special privilege, no cooperation from the victim beyond subscribing to the store, which is the entire point of the mirror mechanism.

### Recommendation
Validate and restrict mirror/server URLs before they are used for outbound fetches: enforce an allow-list of schemes (e.g., `http`/`https`/configured plugin schemes only), reject loopback/link-local/private/multicast/metadata-service address ranges (unless explicitly configured for local testing), and consider resolving hostnames and re-checking the resolved IP before connecting (to prevent DNS-rebinding bypass) in `http_download` (`chia/data_layer/download_data.py`) and in `DataLayer.get_downloader`/`fetch_and_validate` (`chia/data_layer/data_layer.py`). Apply the same validation to `add_mirror` at publication time so malicious mirrors are rejected as early as possible, similar to how `s3_plugin_service.py` already restricts URLs to `s3://` scheme and pre-registered bucket lists.

### Proof of Concept
1. Attacker creates/controls a DataLayer store and calls `add_mirror` with a URL pointing at an internal address, e.g. `http://127.0.0.1:8555/` (a local RPC port) or a cloud metadata IP, using the RPC shown in `chia/data_layer/data_layer_rpc_api.py:468-475`.
2. The URL is published on-chain via `dl_new_mirror` (`chia/wallet/wallet_rpc_api.py:3255-3274`) with no validation.
3. A victim node subscribes to this store id (a normal, unprivileged DataLayer client action).
4. During periodic sync, `update_subscriptions_from_wallet` (`chia/data_layer/data_layer.py:979-985`) imports the malicious URL into the victim's local subscription table.
5. `fetch_and_validate` (`chia/data_layer/data_layer.py:642-705`) selects this server and calls `insert_from_delta_file` → `download_file` → `http_download` (`chia/data_layer/download_data.py:298-324`), causing the victim's DataLayer process to issue an HTTP GET to the attacker-chosen internal URL, confirmable via observed connection attempts/timing differences to the target internal service.

### Citations

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

**File:** chia/data_layer/data_layer.py (L642-705)
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
                if success:
                    self.log.info(
                        f"Finished downloading and validating {store_id}. "
                        f"Wallet generation saved: {singleton_record.generation}. "
                        f"Root hash saved: {singleton_record.root}."
                    )
                    break
            except aiohttp.client_exceptions.ClientConnectorError:
                self.log.warning(f"Server {url} unavailable for {store_id}.")
            except Exception as e:
                self.log.warning(f"Exception while downloading files for {store_id}: {e} {traceback.format_exc()}.")
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

**File:** chia/wallet/wallet_rpc_api.py (L3255-3274)
```python
    async def dl_new_mirror(
        self,
        request: DLNewMirror,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> DLNewMirrorResponse:
        """Add a new on chain message for a specific singleton"""
        if self.service.wallet_state_manager is None:
            raise ValueError("The wallet service is not currently initialized")

        dl_wallet = await self.service.wallet_state_manager.get_dl_wallet()
        async with self.service.wallet_state_manager.lock:
            await dl_wallet.create_new_mirror(
                request.launcher_id,
                request.amount,
                Mirror.encode_urls(request.urls),
                action_scope,
                fee=request.fee,
                extra_conditions=extra_conditions,
            )
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
