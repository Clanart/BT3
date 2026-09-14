### Title
Improper validation of on-chain "mirror" URLs enables SSRF from any spend-bundle submitter against a Data Layer node's outbound HTTP fetch path - (File: `chia/data_layer/download_data.py`, `chia/data_layer/data_layer_wallet.py`)

### Summary
Any unprivileged spend-bundle submitter can create a coin with the well-known, permissionless `create_mirror_puzzle()` puzzle hash and attach arbitrary URL bytes as memos. Every Data Layer node that is syncing/subscribed to the referenced store id ingests that coin as a "mirror," stores the attacker-controlled URL verbatim, and later performs an unauthenticated outbound `aiohttp` GET/POST request to that URL when trying to sync delta files or hand the URL to a downloader plugin — with no validation that the host is not loopback/link-local/internal. This mirrors the "improper domain lookup / no allowlist enforcement leading to SSRF" bug class from the referenced libuv/Node.js CVE-2024-24806 report: an attacker-supplied host string is used to make a server-side request without any check that it targets a disallowed address space.

### Finding Description
The mirror puzzle is a fixed, permissionless puzzle: [1](#0-0) 

Any spend bundle that creates a coin with `CREATE_COIN` targeting `MIRROR_PUZZLE_HASH` and memos `[launcher_id, url1, url2, ...]` is a valid "mirror coin" — this requires no ownership of the DataLayer singleton and no special authorization: [2](#0-1) 

When any node's `DataLayerWallet` observes such a coin for a launcher id it is tracking, it unconditionally records the attacker-chosen URLs (only checking that the list is non-empty): [3](#0-2) 

The `DataLayer` service periodically pulls these mirror URLs from wallet RPC and pushes them into the subscription/server table without any host/scheme validation: [4](#0-3) 

During the sync loop, `fetch_and_validate()` shuffles through these server URLs and issues real outbound HTTP requests to them (or hands them to a downloader plugin) with no restriction on target address: [5](#0-4) 

The actual unauthenticated request is made in `http_download()`, which performs a plain `aiohttp` GET against `server_info.url` — a string fully controlled by whoever created the mirror coin — with no hostname/IP allowlist or denylist check (e.g., no rejection of `127.0.0.1`, `169.254.169.254`, `::1`, or RFC1918 ranges): [6](#0-5) 

The plugin-download path similarly POSTs the attacker-controlled `server_info.url` value to a local downloader plugin as JSON, which then may act on it: [7](#0-6) 

By contrast, the codebase's `chia/util/network.py` `resolve()` helper — used for legitimate peer connections — at least performs DNS/IP resolution logic, but neither it nor the DataLayer HTTP client path enforces any "reject private/loopback/link-local" policy, so there is no SSRF mitigation anywhere in this call chain: [8](#0-7) 

This matches the reported bug class: a network endpoint accepts an externally-supplied host/URL string and performs a server-side request without validating that it doesn't resolve to an internal/forbidden address — the same class of flaw as libuv's improper domain lookup leading to SSRF.

### Impact Explanation
Any party who can broadcast a spend bundle (i.e., anyone, since creating a mirror coin requires only spendable value and no special key/authorization) can force every Data Layer node subscribed to a targeted store id to make server-initiated HTTP requests to attacker-chosen destinations, including:
- Internal-only services on the operator's LAN or loopback interface (e.g., other locally-bound RPC/services),
- Cloud metadata endpoints (e.g., `169.254.169.254`) if the node runs in a cloud VM, potentially exposing instance credentials to a plugin/log side channel,
- Arbitrary external hosts, turning any Data Layer node into a proxy for outbound connections chosen by an unrelated third party (network reconnaissance / amplification).

While the downloaded content generally fails the subsequent Merkle-root/hash validation and is deleted on failure, the SSRF request itself already occurs unconditionally before validation, so information disclosure via timing/side channels and unwanted internal state changes triggered by GET/POST to internal HTTP endpoints are both possible. This satisfies the "concrete... unauthorized... invalid... " impact bar as a genuine SSRF against Data Layer client nodes, reachable purely from a submitted spend bundle — no malicious peer, node, or operator access is required.

### Likelihood Explanation
Likelihood is high: creating a `CREATE_COIN` condition to the fixed `MIRROR_PUZZLE_HASH` with arbitrary memo URLs requires no privileged key, no specific coin ownership beyond enough value to create the coin, and no cooperation from the store's owner. Any node that subscribes to (or is convinced to subscribe to) the targeted store id will automatically ingest and act on the malicious URL during its normal periodic sync loop (`periodically_manage_data` → `update_subscription` → `fetch_and_validate`), with no operator interaction needed.

### Recommendation
- Validate mirror URLs before storing/using them: enforce an allowed scheme (`https`), reject URLs whose resolved host is loopback, link-local, private-range, or otherwise internal (apply an SSRF-safe resolver similar in spirit to `chia.util.network.resolve()` but with an explicit deny-list for internal/reserved address ranges).
- Apply the same validation both when persisting mirrors in `DataLayerWallet.coin_added()` (`chia/data_layer/data_layer_wallet.py`) and immediately before making the outbound request in `http_download()`/`download_file()` (`chia/data_layer/download_data.py`), since the mirror table can be populated well before a request is issued.
- Consider requiring explicit operator opt-in (config allowlist of scheme/host patterns) before any Data Layer node performs outbound HTTP to a chain-supplied mirror URL.

### Proof of Concept
1. Attacker crafts and broadcasts a spend bundle that creates a coin with puzzle hash `MIRROR_PUZZLE_HASH = create_mirror_puzzle().get_tree_hash()` and memos `[victim_launcher_id, b"http://169.254.169.254/latest/meta-data/iam/security-credentials/"]` (or `b"http://127.0.0.1:<internal-port>/..."`).
2. Any Data Layer node tracking `victim_launcher_id` (any node subscribed to that store, which is a normal, permissionless action) observes the coin in `DataLayerWallet.coin_added()`, extracts the URL via `get_mirror_info()`, and stores it as a mirror with no validation.
3. `DataLayer.update_subscriptions_from_wallet()` copies the URL into the subscription's `ServerInfo`.
4. On the next sync cycle, `DataLayer.fetch_and_validate()` selects this `ServerInfo` and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, causing the node to issue an outbound `aiohttp` GET to the attacker-chosen internal/cloud-metadata URL — completing the SSRF.

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L90-94)
```python
def create_mirror_puzzle() -> Program:
    return P2_PARENT.curry(Program.to(1))


MIRROR_PUZZLE_HASH = create_mirror_puzzle().get_tree_hash()
```

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

**File:** chia/data_layer/data_layer_wallet.py (L775-801)
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

**File:** chia/data_layer/data_layer.py (L642-690)
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

**File:** chia/data_layer/download_data.py (L110-168)
```python
async def download_file(
    data_store: DataStore,
    target_filename_path: Path,
    store_id: bytes32,
    root_hash: bytes32,
    generation: int,
    server_info: ServerInfo,
    proxy_url: str | None,
    downloader: PluginRemote | None,
    timeout: aiohttp.ClientTimeout,
    client_foldername: Path,
    timestamp: int,
    log: logging.Logger,
    grouped_by_store: bool,
    group_downloaded_files_by_store: bool,
    max_delta_file_size: int,
) -> bool:
    if target_filename_path.exists():
        return True
    filename = get_delta_filename(store_id, root_hash, generation, grouped_by_store)

    if downloader is None:
        # use http downloader - this raises on any error
        try:
            await http_download(
                target_filename_path, filename, proxy_url, server_info, timeout, log, max_delta_file_size
            )
        except (asyncio.TimeoutError, aiohttp.ClientError, MaxDeltaFileSizeExceededError):
            new_server_info = await data_store.server_misses_file(store_id, server_info, timestamp)
            log.info(
                f"Failed to download {filename} from {new_server_info.url}."
                f"Miss {new_server_info.num_consecutive_failures}."
            )
            log.info(f"Next attempt from {new_server_info.url} in {new_server_info.ignore_till - timestamp}s.")
            return False
        return True

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

**File:** chia/util/network.py (L152-177)
```python
async def resolve(host: str, *, prefer_ipv6: bool = False) -> IPAddress:
    try:
        return IPAddress.create(host)
    except ValueError:
        pass
    addrset = await asyncio.get_event_loop().getaddrinfo(host, None)
    # The list returned by getaddrinfo is never empty, an exception is thrown or data is returned.
    ips_v4 = []
    ips_v6 = []
    for family, _, _, _, ip_port in addrset:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue  # pragma: no cover
        host_ip = ip_port[0]
        assert isinstance(host_ip, str)
        ip = IPAddress.create(host_ip)
        if family == socket.AF_INET:
            ips_v4.append(ip)
        else:
            ips_v6.append(ip)
    preferred, alternative = (ips_v6, ips_v4) if prefer_ipv6 else (ips_v4, ips_v6)
    if len(preferred) > 0:
        return preferred[0]
    elif len(alternative) > 0:
        return alternative[0]
    else:
        raise ValueError(f"failed to resolve {host} into an IP address")
```
