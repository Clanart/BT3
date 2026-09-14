Confirmed: there is no hostname/IP validation anywhere in the DataLayer mirror/download path (`data_store.py`, `download_data.py`, `data_layer.py`) — no `urlparse`/`ip_address`/private-range checks before `http_download()` performs `aiohttp` requests to a mirror's `server_info.url`.

### Title
Unvalidated Mirror/Server URLs in DataLayer Enable SSRF Against Internal Services and DataLayer RPC/Plugin Endpoints - (File: `chia/data_layer/download_data.py`)

### Summary
Any wallet user can publish an arbitrary URL on-chain as a DataLayer "mirror" for any store via the `dl_new_mirror` wallet RPC (`chia/wallet/wallet_rpc_api.py:3255` calling `DataLayerWallet.create_new_mirror`, `chia/data_layer/data_layer_wallet.py:687-703`). Every DataLayer node that subscribes to (or owns/tracks) that store treats the mirror's `urls` as trusted server endpoints and fetches from them with no host/IP validation, directly analogous to the `SendWebRequestBlock` SSRF bug class in the AutoGPT advisory (missing `_is_ip_blocked()`-style checks before an outbound authenticated fetch).

### Finding Description
`DataLayer.update_subscriptions_from_wallet()` pulls mirror URLs straight from chain-published `Mirror` records with no filtering: [1](#0-0) 

Those URLs flow into `DataStore.subscribe`/`get_available_servers_for_store` as `ServerInfo.url` and are later used unchanged by `fetch_and_validate()`: [2](#0-1) 

The actual network fetch, `http_download()`, builds the request directly from `server_info.url` with **no scheme allow-list, no hostname resolution check, and no private/loopback/link-local/CGNAT (`100.64.0.0/10`) blocking** — the same class of gap described in the CVE (`_is_ip_blocked()` not normalizing/blocking special-use ranges): [3](#0-2) 

`download_file()` similarly forwards `server_info.url` verbatim to a downloader plugin, and even the plugin download path (`handle_download`) only checks the URL scheme is `s3` and matches a configured store URL — it never restricts the destination network: [4](#0-3) 

Mirror creation itself performs no URL validation beyond checking the list is non-empty: [5](#0-4) 

Existing project utilities that *do* implement IP-range/localhost checks (`chia/util/network.py` `is_in_network`, `is_localhost`, `is_trusted_cidr`, and `chia/util/ip_address.py`'s `is_private`) are used for peer connection trust decisions elsewhere, but are never applied to DataLayer mirror/server URLs before an outbound HTTP fetch is issued: [6](#0-5) 

Any wallet holding a small amount of XCH can call `dl_new_mirror` for **any store_id**, including stores the caller doesn't own, and set the URL to an internal address (e.g. cloud metadata endpoint, an operator's DataLayer/wallet/full-node RPC bound to a private interface, or another internal service). Every peer node that syncs/tracks that DataLayer store (which happens automatically for subscribers, and via `periodically_manage_data()`/`fetch_and_validate()` background loop) will issue authenticated-context outbound HTTP GET/POST requests to that internal target from the DataLayer service process.

### Impact Explanation
This allows a low-privileged, unauthenticated-to-target user (any wallet holder able to submit a `dl_new_mirror` transaction) to coerce arbitrary DataLayer node operators' processes into making HTTP requests to internal-only network endpoints (localhost services, cloud metadata services, LAN RPC ports, CGNAT ranges) that are otherwise unreachable from the public internet. Depending on what's reachable inside the operator's network, this can expose internal RPC responses (data exfiltration), reach other unauthenticated internal services, or be chained with additional bugs in an internal target (RPC without auth, metadata credential leak) to escalate further — mirroring the "SSRF-to-RCE" severity rationale in the source advisory, though the ultimate blast radius depends on what is reachable from the affected node's network position.

### Likelihood Explanation
Likelihood is high: publishing a mirror only requires a wallet transaction with a mirror-coin amount and fee — no special privilege, ownership of the target store, or admin access is required. The DataLayer background sync loop (`periodically_manage_data()`) automatically discovers and uses mirror URLs for any tracked/subscribed store without user interaction, so exploitation requires no additional action once a node begins tracking the malicious store (e.g., via legitimate curiosity, an offer that references the store, or default auto-subscription behavior described in `.cursor/context/data-layer.md`).

### Recommendation
Before issuing any outbound HTTP request driven by a mirror/server URL or downloader plugin URL (`http_download()`, `download_file()`, `get_downloader()` in `chia/data_layer/download_data.py` / `chia/data_layer/data_layer.py`), resolve the hostname and validate the resulting IP(s) against a deny-list of private, loopback, link-local, multicast, and CGNAT (`100.64.0.0/10`) ranges — reusing/extending `chia/util/network.py`'s `is_in_network`/`is_localhost` and `chia/util/ip_address.py`'s `is_private` helpers, and importantly normalizing IPv4-mapped IPv6 addresses (`::ffff:x.x.x.x`) before the check, per the root cause called out in the referenced CVE. Consider also restricting `dl_new_mirror` URL schemes and requiring operator opt-in/allow-listing for mirror hosts that aren't well-known.

### Proof of Concept
1. Attacker wallet calls `dl_new_mirror` RPC for any known `launcher_id` (does not need to own the store) with `urls=["http://169.254.169.254/latest/meta-data/"]` (or an internal RPC address like `http://127.0.0.1:8562/...` on the victim's network) and pushes the transaction on-chain, as demonstrated by the test flow in `chia/_tests/wallet/rpc/test_dl_wallet_rpc.py:281-292`.
2. Any DataLayer node tracking/subscribed to that `launcher_id` runs `update_subscriptions_from_wallet()` (`chia/data_layer/data_layer.py:979-985`), pulling in the attacker's URL as a `ServerInfo`.
3. That node's periodic sync calls `fetch_and_validate()` → `insert_from_delta_file()` → `download_file()`/`http_download()` (`chia/data_layer/download_data.py:298-319`), issuing an outbound GET to the attacker-chosen internal URL with no IP/host validation, and the response content/behavior is observable via failure/success signaling (`server_misses_file`/`received_correct_file` state, timing, and logs) or exfiltrated data if the target endpoint's response ends up in a downloadable delta file path.

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

**File:** chia/data_layer/download_data.py (L110-146)
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

**File:** chia/util/network.py (L118-141)
```python
def is_in_network(peer_host: str, networks: Iterable[IPv4Network | IPv6Network]) -> bool:
    try:
        peer_host_ip = ip_address(peer_host)
        return any(peer_host_ip in network for network in networks)
    except ValueError:
        return False


def is_trusted_cidr(peer_host: str, trusted_cidrs: list[str]) -> bool:
    try:
        ip_obj = ipaddress.ip_address(peer_host)
    except ValueError:
        return False

    for cidr in trusted_cidrs:
        network = ipaddress.ip_network(cidr)
        if ip_obj in network:
            return True

    return False


def is_localhost(peer_host: str) -> bool:
    return peer_host in {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}
```
