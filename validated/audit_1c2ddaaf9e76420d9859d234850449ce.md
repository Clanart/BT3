### Title
Unvalidated on-chain mirror URLs allow Data Layer clients to be coerced into SSRF against internal hosts - (File: chia/data_layer/download_data.py)

### Summary
The Data Layer mirror mechanism lets any wallet user publish an arbitrary URL to the chain for a given store, and every other Data Layer client that syncs that store will later issue an HTTP GET to that exact URL with no hostname/IP validation, no DNS-resolution check, and no scheme restriction (`http://` is allowed). This mirrors the CVE-2026-73160 bug class: attacker-controlled hostnames/URLs are trusted and fetched by the server-side component without checking that the resolved address is not private/loopback/link-local.

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet holder publish one or more arbitrary strings as `urls` in a mirror coin's memo, with no format or destination validation: [1](#0-0) 

The RPC surface `add_mirror` / `dl_new_mirror` passes the caller-supplied `urls` straight through, again with no host/IP checks (only that the list isn't empty): [2](#0-1) [3](#0-2) 

Once the mirror coin is confirmed on-chain, any node subscribed to that `store_id` treats the mirror URL as a valid download source: `fetch_and_validate()` iterates `servers_info` (built from confirmed mirrors) and calls `insert_from_delta_file()`, which calls `download_file()` → `http_download()`: [4](#0-3) [5](#0-4) 

`http_download()` performs a plain `aiohttp` GET against `server_info.url + "/" + filename` with no check that the URL's host resolves outside private/loopback/link-local/reserved ranges — unlike `chia/util/network.py`'s `resolve()`/`is_in_network()`/`is_localhost()` helpers used elsewhere in the codebase for peer-network trust decisions, none of that logic is applied here: [6](#0-5) 

The `get_downloader()` / plugin-remote path (`handle_download`) similarly issues arbitrary `aiohttp` POST requests to plugin/downloader URLs based on config, but the mirror-driven `http_download` path is the one attacker (mirror publisher) controlled end-to-end.

### Impact Explanation
Any wallet user who owns/controls a Data Layer store (or simply has enough XCH to create a mirror coin) can publish a mirror URL pointing at an internal address (e.g. `http://127.0.0.1:<port>/`, `http://169.254.169.254/`, or an internal service on the operator's LAN/host). Every peer node that later subscribes to or syncs that store's DataLayer service will have its `data_layer` process issue outbound HTTP requests to that internal target when trying to fetch delta files, with the request path built from `store_id`/`node_hash`/generation values that the attacker also fully controls (`get_delta_filename`). This is server-side request forgery: the DataLayer service becomes a proxy that an unprivileged, remote/unauthenticated (from the target's perspective) party can direct against internal-only endpoints reachable from the victim node's host, without any egress authentication. Depending on what's reachable internally (local RPC ports, cloud metadata service, docker/kubernetes control-plane, other operator services on localhost), the request itself and its side effects (issuing GET) can be abused, and the requester will also observe HTTP-level success/failure signals (timing, status via failure logging) that leak information about what's alive inside the victim network.

### Likelihood Explanation
Likelihood is Medium: creating a mirror coin requires only a normal DL wallet spend (cheap, unprivileged) and no special role; the victim behavior (fetching deltas from mirrors of a subscribed store) is a normal, automatic part of `fetch_and_validate()`'s periodic sync loop, so a store owner does not need to social-engineer anything beyond getting a peer to subscribe/track the store (which is a supported, expected DataLayer workflow — e.g. via offers involving that store or public mirror listings). No authentication or peer trust bypass is required.

### Recommendation
Before dereferencing mirror/downloader URLs in `http_download()`/`get_downloader()`, resolve the hostname (e.g. via `socket.getaddrinfo`) and reject any URL whose resolved address is private, loopback, link-local, or otherwise non-globally-routable, reusing/extending the existing `chia/util/network.py` helpers (`is_localhost`, `is_in_network`, or a new `is_global`-style check) at both mirror-add time (defense in depth / early UX rejection) and, more importantly, at fetch time in `download_data.py` immediately before each `aiohttp` request is issued, since DNS can change between publish and fetch time.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `dl_new_mirror` (via `chia data add_mirror` or the wallet RPC) with `urls=["http://127.0.0.1:<victim-local-service-port>"]` — see `add_mirror` flow: `chia/data_layer/data_layer_rpc_api.py` lines 468-475 → `chia/data_layer/data_layer.py` lines 965-970 → `chia/wallet/wallet_rpc_api.py` lines 3255-3274.
2. The mirror coin confirms on-chain; any other operator's node that tracks/subscribes to this `store_id` (a standard DataLayer operation) will have its own `data_layer` service call `fetch_and_validate()` → `insert_from_delta_file()` → `download_file()` → `http_download()` (`chia/data_layer/data_layer.py` lines 642-694; `chia/data_layer/download_data.py` lines 298-319), issuing `GET http://127.0.0.1:<port>/<store_id>-<node_hash>-delta-<generation>-v1.0.dat` from the victim node's own machine.
3. No code path validates that the mirror URL's resolved address is outside private/loopback/reserved ranges, so the request reaches the internal target.

### Citations

**File:** chia/data_layer/data_layer_wallet.py (L687-704)
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
