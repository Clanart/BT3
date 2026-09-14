No URL validation (no scheme allowlist, no private-IP/loopback/metadata-address blocklist) exists anywhere in `chia/data_layer/` for mirror URLs before they are used for outbound HTTP fetches, confirming the SSRF path is unmitigated.

### Title
Unvalidated on-chain mirror URLs enable SSRF and file-size/availability DoS in DataLayer sync - (File: chia/data_layer/download_data.py)

### Summary
DataLayer "mirror" coins let any wallet holder attach arbitrary URL strings to a public DataLayer store id. These URLs are later fetched by any node that subscribes to that store, with no scheme or destination validation, allowing an attacker to force victim DataLayer nodes to issue outbound HTTP requests to attacker-chosen internal/loopback/metadata endpoints (SSRF) and to degrade sync availability by publishing junk/slow/oversized mirror endpoints.

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a standard coin spend whose only "mirror" payload is a free-form list of URL bytes placed in the coin memo, with no format or destination restriction: [1](#0-0) . This is exposed unauthenticated to any wallet user via the `dl_new_mirror` RPC, which likewise performs no URL validation before pushing the transaction: [2](#0-1) . Any coin owner can create such a mirror for **any** launcher/store id (there is no ownership check requiring the mirror creator to be the store owner), so an attacker can attach malicious URLs to a public store that other participants subscribe to.

On the reading side, `DataLayer.update_subscriptions_from_wallet()` pulls these mirror URLs verbatim off-chain state and stores them as subscription server candidates: [3](#0-2) . `fetch_and_validate()` then iterates these server URLs and calls into the download path: [4](#0-3) . The actual network fetch, `http_download()`, issues an `aiohttp` GET directly to `server_info.url + "/" + filename` with no host/IP allow-listing, no restriction on private, loopback, or link-local addresses (e.g. `127.0.0.1`, `169.254.169.254` cloud metadata, internal-network hosts), and only a soft size guard based on `content-length`/streamed byte count: [5](#0-4) . Tests explicitly show mirror URLs such as `http://127.0.0.1/8000` being accepted and used without any rejection, confirming there is no validation layer anywhere in this pipeline: [6](#0-5) .

The project's own internal documentation acknowledges mirror/plugin URLs are untrusted "external trust inputs," but only in the context of data integrity (verifying downloaded content against wallet-advertised roots) — not in the context of restricting *where* the node is allowed to send outbound requests: [7](#0-6) .

### Impact Explanation
Any Data Layer client node that subscribes to a store (an intended, expected DataLayer workflow) can be coerced into issuing outbound HTTP requests to attacker-chosen destinations reachable from that node's network context — including internal services, loopback-bound admin interfaces, or cloud instance metadata endpoints — solely because an unprivileged wallet holder attached a malicious mirror URL on-chain to that store. Beyond SSRF, an attacker-controlled or attacker-poisoned mirror can also degrade node behavior: a store's `get_available_servers_for_store()` set is randomized and iterated (`fetch_and_validate`), so slow-responding or failure-inducing endpoints delay legitimate sync (mitigated somewhat by per-server ban/backoff logic on failures, but still causing repeated retries/exceptions across the full server list before backoff kicks in).

### Likelihood Explanation
Likelihood is high for the SSRF component: creating a mirror costs only a small transaction fee, requires no special privilege, and any store id (owned or not) can be targeted, since `create_new_mirror()`/`dl_new_mirror` do not check that the caller owns the referenced launcher. The victim-side trigger only requires that some Data Layer participant subscribes to the affected store, which is the normal use case for public DataLayer stores.

### Recommendation
Validate mirror URLs before they are persisted/used in `create_new_mirror()`/`dl_new_mirror` and, defensively, again in `http_download()`/`download_file()`: restrict to `http`/`https` schemes, resolve and reject requests to loopback, link-local, private (RFC1918), and other non-public/reserved address ranges, and enforce a hard timeout/size cap independent of server-supplied headers. Consider requiring mirror creation to be gated by store ownership or otherwise rate-limited.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` (or CLI `add_mirror`) targeting a victim's/any public store's `launcher_id` with `urls=["http://127.0.0.1:PRIVILEGED_PORT/admin"]` or `urls=["http://169.254.169.254/latest/meta-data/"]`, as shown structurally in [8](#0-7) , paying only the mirror amount + fee.
2. Once the mirror coin confirms on chain, any victim DataLayer node that has (or later creates) a subscription to that `launcher_id` calls `update_subscriptions_from_wallet()`, ingesting the attacker URL as a legitimate server candidate: [3](#0-2) .
3. On the victim's next `fetch_and_validate()` cycle, the DataLayer service issues an outbound `aiohttp` request to the attacker-chosen URL via `http_download()`, with no destination validation: [9](#0-8) .

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

**File:** chia/data_layer/download_data.py (L298-345)
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
            progress_byte = 0
            progress_percentage = f"{0:.0%}"
            success = False
            try:
                with target_filename_path.open(mode="wb") as f:
                    async for chunk, _ in resp.content.iter_chunks():
                        f.write(chunk)
                        progress_byte += len(chunk)
                        if progress_byte > max_delta_file_size_bytes:
                            raise MaxDeltaFileSizeExceededError(
                                f"Maximum delta file size exceeded: {max_delta_file_size} MiB."
                            )
                        if size > 0:
                            new_percentage = f"{progress_byte / size:.0%}"
                            if new_percentage != progress_percentage:
                                progress_percentage = new_percentage
                                log.info(f"Downloading delta file {filename}. {progress_percentage} of {size} bytes.")
                success = True
            finally:
                if not success:
                    target_filename_path.unlink(missing_ok=True)
```

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2767-2801)
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

        res = await data_rpc_api.delete_mirror({"coin_id": coin_id, "fee": 1})
        await farm_block_check_singleton(data_layer, full_node_api, ph, store_id, wallet=wallet_rpc_api.service)
        mirrors = await data_rpc_api.get_mirrors({"id": store_id.hex()})
        mirror_list = mirrors["mirrors"]
        assert len(mirror_list) == 0

        with pytest.raises(RuntimeError, match="URL list can't be empty"):
            res = await data_rpc_api.add_mirror({"id": store_id.hex(), "urls": [], "amount": 1, "fee": 1})

```

**File:** .cursor/context/data-layer.md (L83-90)
```markdown
## Fragility Hotspots

- High-risk edits move work across DB writer transactions, `_update_confirmation_status()`, pending-root status changes, or wallet RPC calls. These boundaries encode publication and rollback assumptions.
- File-system writes for Merkle blobs, key/value blobs, and `.dat` files have TODOs around locking. Concurrent service/plugin/server access should be treated as a real consistency concern.
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
- DataLayer wallet code depends on singleton CLVM structure, odd singleton amounts, lineage proofs, and offer solver field names. Changes in wallet puzzle drivers or offer summaries can break this module without direct edits here.
- Tests for this module should cover lifecycle/order effects: pending batch to publish, confirmation promotion, wallet-unreachable sync retry, delta-file validation failure, grouped vs ungrouped filenames, unsubscribe retention, historical proof verification, and offer-created local mutations.
```

**File:** chia/_tests/wallet/rpc/test_dl_wallet_rpc.py (L281-292)
```python
            txs = (
                await client.dl_new_mirror(
                    DLNewMirror(
                        launcher_id=launcher_id,
                        amount=uint64(1000),
                        urls=["foo", "bar"],
                        fee=uint64(2000000000000),
                        push=True,
                    ),
                    DEFAULT_TX_CONFIG,
                )
            ).transactions
```
