### Title
Unvalidated Outbound HTTP Fetch of Attacker-Supplied DataLayer Mirror URLs Enables SSRF Against Local/Internal Services - (File: chia/data_layer/download_data.py)

### Summary
`create_new_mirror()` in `chia/data_layer/data_layer_wallet.py` lets **any** wallet holder spend their own coins to create a DataLayer "mirror" coin for **any arbitrary `launcher_id`** (no ownership check tying the mirror to the caller's own store), embedding an attacker-chosen list of URL strings as coin memos. Those URLs are later fetched by every DataLayer node subscribed to that store via `http_download()` in `chia/data_layer/download_data.py`, using plain `aiohttp.ClientSession().get(server_info.url + "/" + filename, ...)` with **zero host/IP validation** — not even the (bypassable) localhost check that Zitadel attempted. This is a stronger instance of the same bug class as GHSA-6cf5-w9h3-4rqv: user-controlled destination URLs are fetched by a service process without restricting requests to internal/loopback/link-local targets.

### Finding Description
- `dl_new_mirror` (`chia/wallet/wallet_rpc_api.py:3255`) → `DataLayerWallet.create_new_mirror` (`chia/data_layer/data_layer_wallet.py:687-703`) builds a standard coin spend to `create_mirror_puzzle().get_tree_hash()` with `memos=[[launcher_id, *urls]]`. There is no check that `launcher_id` belongs to a store owned/tracked by the caller — any `launcher_id` value (including one for a store the attacker doesn't own) can be used.
- On the receiving side, `DataLayerWallet.coin_added()` (`chia/data_layer/data_layer_wallet.py:775-799`) accepts any mirror coin whose parent-spend memos decode a tracked `launcher_id`, storing the attacker-supplied URLs unconditionally: `if await self.wallet_state_manager.dl_store.is_launcher_tracked(launcher_id): ... await self.wallet_state_manager.dl_store.add_mirror(Mirror(coin.name(), launcher_id, uint64(coin.amount), Mirror.decode_urls(urls), ours, height))`.
- `DataLayer.update_subscriptions_from_wallet()` (`chia/data_layer/data_layer.py:979-985`) pulls all mirrors for a store from the wallet (`dl_get_mirrors`) and pushes their raw URLs into the local subscription server list with no sanitization: `urls += mirror.urls`.
- `DataLayer.fetch_and_validate()` (`chia/data_layer/data_layer.py:606-706`) iterates `servers_info` (populated from those subscription URLs) and calls `insert_from_delta_file(...)` → `download_file(...)` → `http_download()` (`chia/data_layer/download_data.py:298-345`), which performs `session.get(server_info.url + "/" + filename, ...)` directly. There is no `is_localhost()`, `is_in_network()`, or any IP/host allow-list check anywhere in this path (compare with `chia/util/network.py`'s `is_localhost`/`is_in_network`/`is_trusted_cidr`, which are used for P2P peer admission but never for DataLayer HTTP fetch targets).
- Any full-node/wallet user running a DataLayer service and syncing/subscribing to a store that has an attacker-created mirror will therefore have their process issue outbound HTTP requests to attacker-chosen hosts/ports — including `127.0.0.1`, RFC1918 ranges, cloud metadata endpoints, or other locally-bound services (e.g., the node's own RPC ports), fully analogous to the Zitadel `isHostBlocked` bypass, except here there is no block to bypass at all.

### Impact Explanation
An unprivileged wallet user can weaponize the DataLayer mirror mechanism as a generalized SSRF primitive against any operator running a DataLayer service that subscribes to (or tracks mirrors for) the targeted store. This can be used to probe/interact with internal-only services (local RPC endpoints, metadata services, other daemons bound to loopback/private addresses) reachable from the victim's node, potentially leaking sensitive local data (e.g., RPC responses) or triggering unintended state changes on those internal services, depending on what is reachable. It does not directly cause coin-set divergence or fund theft, but it is a concrete confidentiality/integrity impact against operator infrastructure triggered purely by an on-chain, attacker-controlled coin spend — a legitimate "spend-triggered" analog of the reported bug class.

### Likelihood Explanation
Likelihood is high for any DataLayer operator who subscribes to third-party stores or who tracks mirrors for stores they don't fully control: the mirror-creation transaction requires only a small XCH amount and a normal wallet spend (`dl_new_mirror` RPC), no special privileges, and no interaction with the store owner. The periodic sync loop (`periodically_manage_data()`/`update_subscription()`) automatically ingests and fetches from any tracked mirror's URLs without any manual approval step, making exploitation largely automatic once a node begins tracking/subscribing to the attacker-targeted store.

### Recommendation
- Restrict `create_new_mirror`/`dl_new_mirror` mirror creation so that URLs are validated (scheme allow-list, DNS-resolved IP checked against private/loopback/link-local ranges) before being persisted or used, reusing the existing `chia.util.network.is_localhost` / `is_in_network` helpers.
- Apply the same validation at fetch time in `http_download()`/`download_file()` in `chia/data_layer/download_data.py`, resolving the mirror host and rejecting requests whose resolved address is loopback, link-local, or in RFC1918/RFC4193 private ranges (unless explicitly configured/opted-in for local testing), guarding against DNS-rebinding by validating the connected socket's peer address rather than only the DNS answer.
- Consider requiring operator-side allow-listing/opt-in for mirror URLs discovered from stores the node does not own, rather than auto-fetching from any tracked mirror.

### Proof of Concept
1. Attacker runs a standard wallet and calls the `dl_new_mirror` RPC with `launcher_id` set to the victim's targeted (tracked) DataLayer store, `urls=["http://127.0.0.1:8555/some_internal_rpc_path"]` (or a private/internal IP of the victim's infrastructure), and a nominal `amount`/`fee`.
2. The resulting mirror coin is confirmed on-chain; any node tracking that `launcher_id` (via `is_launcher_tracked`) ingests the mirror through `coin_added()` and stores the URL unfiltered.
3. When the victim's DataLayer service next runs `update_subscriptions_from_wallet()`/`fetch_and_validate()` for that store, `http_download()` issues `GET http://127.0.0.1:8555/some_internal_rpc_path/<filename>` from the victim's process — reaching whatever service is bound to that internal address, with no host-validation check ever attempted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** chia/data_layer/data_layer_wallet.py (L775-799)
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
```

**File:** chia/data_layer/data_layer.py (L642-706)
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

**File:** chia/data_layer/download_data.py (L298-323)
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
```

**File:** chia/wallet/wallet_rpc_api.py (L3255-3277)
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

        # tx_endpoint will take care of default values here
        return DLNewMirrorResponse(unsigned_transactions=[], transactions=[])
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
