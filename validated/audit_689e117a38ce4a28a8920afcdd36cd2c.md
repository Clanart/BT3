### Title
DataLayer Mirror URLs Enable Unauthenticated Blind SSRF via `/loadIG`-style Unvalidated Fetch in `http_download()` — ([File: chia/data_layer/download_data.py])

### Summary
Chia's DataLayer mirror mechanism lets any wallet user publish an arbitrary string as a "mirror URL" for a DataLayer store by creating an on-chain mirror coin. Any node that subscribes to that store id will automatically fetch data from that attacker-supplied URL with zero validation of scheme, host, or IP range — the same root cause class as the FHIR Validator `/loadIG` SSRF (CVE-2026-34360): a fully attacker-controlled URL reaches a server-side HTTP client with no allowlist and no private/loopback/link-local IP filtering.

### Finding Description
A mirror URL is created via `dl_new_mirror` RPC, which calls `DataLayerWallet.create_new_mirror()` and creates an on-chain coin whose `CREATE_COIN` memo encodes an arbitrary, attacker-chosen URL string: [1](#0-0) 

Any Chia node syncing the chain picks this coin up in `DataLayerWallet.coin_added()`, decodes the memos with `get_mirror_info()`, and stores the URL unvalidated in the wallet's mirror table: [2](#0-1) 

The DataLayer RPC `add_mirror` handler and service method only check that the URL list is non-empty — no scheme, host, or IP validation is performed: [3](#0-2) 

When a DataLayer client (any local user of the DataLayer RPC, e.g. via `subscribe`) subscribes to that store id, the periodic sync loop calls `update_subscriptions_from_wallet()` to pull those attacker-controlled URLs into the local subscription table, then `fetch_and_validate()` shuffles and iterates these server URLs and passes them straight into `insert_from_delta_file()` → `download_file()`: [4](#0-3) 

`download_file()` calls `http_download()`, which issues an unauthenticated, unvalidated `aiohttp` GET request built by directly concatenating the attacker-supplied `server_info.url`: [5](#0-4) 

There is no check anywhere in this path — `add_mirror`, `update_subscriptions_from_wallet`, `fetch_and_validate`, `download_file`, or `http_download` — that rejects loopback (`127.0.0.1`), link-local (`169.254.169.254`), site-local (RFC1918), or otherwise internal/private targets, and no domain allowlist analogous to `inAllowedPaths()` exists in this code path. This mirrors the exact root-cause chain in the FHIR advisory: user-controlled URL → no host/IP validation → direct outbound HTTP request → error-based information leakage back to the mirror-registering party via server logs/behavior differences (timeouts vs. connection-refused vs. successful-but-invalid-delta-file errors), which are in turn recorded per-server in `data_store.server_misses_file()` / `received_correct_file()` and are also queryable by any local RPC caller via `get_subscriptions()`.

### Impact Explanation
An unprivileged attacker who can create/own a DataLayer singleton (a normal, unprivileged wallet action requiring only a small fee) can register mirror URLs pointing at:
- Cloud metadata services (`169.254.169.254`) or other internal hosts reachable from any victim node that chooses to subscribe to the attacker's store.
- Arbitrary internal ports, enabling port-scan-style reconnaissance through differing failure modes surfaced in DataLayer subscription state (`num_consecutive_failures`, `ignore_till`) and logs, which are queryable via the `get_subscriptions` RPC.

This is a blind SSRF from the victim node's own network position, matching the "Data Layer client" attack surface explicitly in scope. Because every subscribed store's mirrors are fetched automatically and periodically (`periodically_manage_data()`), the amplification/reconnaissance value described in the original advisory (repeated automatic outbound probing) applies directly here too.

### Likelihood Explanation
Creating a mirror coin with an arbitrary URL requires no special privilege beyond paying a normal transaction fee — any wallet user can do it. The only precondition is that a victim's DataLayer node subscribes to the malicious store id, which is a standard, expected DataLayer client action (subscribing to third-party stores for replication is the intended use case), making this a realistically reachable, medium-likelihood issue rather than a purely theoretical one.

### Recommendation
1. Validate mirror URLs both at creation time (`add_mirror`/`create_new_mirror`) and at fetch time (`http_download`/`download_file`): reject non-`http(s)` schemes and resolve the host to reject loopback, link-local, and private/site-local IP ranges before issuing the request, similar to the recommended fix for the FHIR SSRF.
2. Enforce this validation at the point of consumption (`http_download()` in `chia/data_layer/download_data.py`) rather than relying solely on client-side checks, since mirror URLs are received from arbitrary on-chain coins created by any party.
3. Consider adding an optional domain allowlist config for DataLayer mirror fetches, mirroring `ManagedWebAccess.inAllowedPaths()` from the referenced advisory, and ensure the check also applies to any redirect targets if HTTP redirects are followed.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `dl_new_mirror` with a malicious URL:
```
dl_new_mirror(launcher_id=<attacker_store_id>, amount=..., urls=["http://169.254.169.254/latest/meta-data/"], fee=...)
```
This is accepted unchanged by `DataLayer.add_mirror()` (only checks the list is non-empty) — [6](#0-5) .

2. Victim's DataLayer client subscribes to `attacker_store_id` (normal replication use case).

3. During the victim's next `periodically_manage_data()` cycle, `update_subscription()` calls `update_subscriptions_from_wallet()` to import the malicious URL, then `fetch_and_validate()` picks it as a `server_info.url` candidate and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which issues:
```
GET http://169.254.169.254/latest/meta-data/<delta-filename>
```
from the victim node's own network position — [7](#0-6) .

4. The victim can observe reachability/timing differences in DataLayer subscription state (`sinfo.num_consecutive_failures`, `ignore_till`) via `get_subscriptions`, and the attacker (as the store's mirror-registering party) can similarly infer probing outcomes indirectly through mirror/store activity, replicating the blind-SSRF reconnaissance impact of the original advisory.

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L97-110)
```python
def get_mirror_info(
    parent_puzzle: Program | SerializedProgram, parent_solution: Program | SerializedProgram
) -> tuple[bytes32, list[bytes]]:
    assert type(parent_puzzle) is type(parent_solution)
    _, conditions = run_with_cost(parent_puzzle, INFINITE_COST, parent_solution)
    for condition in conditions.as_iter():
        if (
            condition.first().as_python() == ConditionOpcode.CREATE_COIN
            and condition.at("rf").as_python() == create_mirror_puzzle().get_tree_hash()
        ):
            memos: list[bytes] = condition.at("rrrf").as_python()
            launcher_id = bytes32(memos[0])
            return launcher_id, [url for url in memos[1:]]
    raise ValueError("The provided puzzle and solution do not create a mirror coin")
```

**File:** chia/data_layer/data_layer_wallet.py (L775-800)
```python
    async def coin_added(
        self, coin: Coin, height: uint32, peer: WSChiaConnection, coin_data: object | None, sync_scope: WalletSyncScope
    ) -> None:
        if coin.puzzle_hash == create_mirror_puzzle().get_tree_hash():
            parent_state: CoinState = (
                await self.wallet_state_manager.wallet_node.get_coin_state([coin.parent_coin_info], peer=peer)
            )[0]
            parent_spend = await fetch_coin_spend(height, parent_state.coin, peer)
            assert parent_spend is not None
            launcher_id, urls = get_mirror_info(parent_spend.puzzle_reveal, parent_spend.solution)
            # Don't track mirrors with empty url list.
            if not urls:
                return
            if await self.wallet_state_manager.dl_store.is_launcher_tracked(launcher_id):
                ours: bool = await self.wallet_state_manager.get_wallet_for_coin(coin.parent_coin_info) is not None
                await self.wallet_state_manager.dl_store.add_mirror(
                    Mirror(
                        coin.name(),
                        launcher_id,
                        uint64(coin.amount),
                        Mirror.decode_urls(urls),
                        ours,
                        height,
                    )
                )
                await self.wallet_state_manager.add_interested_coin_ids([coin.name()])
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

**File:** chia/data_layer/data_layer.py (L965-985)
```python
    async def add_mirror(self, store_id: bytes32, urls: list[str], amount: uint64, fee: uint64) -> None:
        if not urls:
            raise RuntimeError("URL list can't be empty")
        await self.wallet_rpc.dl_new_mirror(
            DLNewMirror(launcher_id=store_id, amount=amount, urls=urls, fee=fee, push=True), DEFAULT_TX_CONFIG
        )

    async def delete_mirror(self, coin_id: bytes32, fee: uint64) -> None:
        await self.wallet_rpc.dl_delete_mirror(DLDeleteMirror(coin_id=coin_id, fee=fee, push=True), DEFAULT_TX_CONFIG)

    async def get_mirrors(self, store_id: bytes32) -> list[Mirror]:
        mirrors: list[Mirror] = (await self.wallet_rpc.dl_get_mirrors(DLGetMirrors(launcher_id=store_id))).mirrors
        return [mirror for mirror in mirrors if mirror.urls]

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
