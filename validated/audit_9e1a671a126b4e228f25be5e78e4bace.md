### Title
DataLayer mirror URLs are attacker-controlled with no private/internal address filtering, enabling SSRF against subscribing nodes - ([File: chia/data_layer/data_layer_wallet.py], [File: chia/data_layer/download_data.py], [File: chia/data_layer/data_layer.py])

### Summary
Any wallet user can create an on-chain "mirror" coin carrying arbitrary URL strings for *any* DataLayer launcher id, including one they do not own. `DataLayerWallet.coin_added()` accepts and stores these attacker-supplied URLs for any launcher id the local wallet tracks, without checking that the memo's `launcher_id` is owned by (or otherwise associated with) the party creating the mirror coin. Those URLs then flow, completely unfiltered, into `DataLayer.fetch_and_validate()` / `download_data.http_download()` / `get_downloader()`, which issue outbound `aiohttp` HTTP requests to whatever host is specified. There is no equivalent of `private_address_check`-style filtering anywhere in this pipeline — no rejection of loopback, link-local, RFC1918, or cloud metadata addresses (e.g. `169.254.169.254`), and no restriction on URL scheme/port.

### Finding Description
`DataLayerWallet.coin_added()` is the sync hook invoked when a coin matching the mirror puzzle hash (`create_mirror_puzzle().get_tree_hash()`) is seen on chain: [1](#0-0) 

The only gating condition is `is_launcher_tracked(launcher_id)` — i.e. whether *this node* is tracking/subscribed to that launcher id — not whether the mirror-coin creator is the launcher's owner. `launcher_id` and `urls` are decoded straight out of the spend's memo (`get_mirror_info`), which is fully controlled by whoever creates the mirror coin (a standard, unprivileged spend). Any user can therefore inject arbitrary mirror URLs into any node's DataLayer mirror table for a store the node is subscribed to, without owning that store.

These URLs are then used unmodified in the DataLayer sync loop. `update_subscriptions_from_wallet()` copies mirror URLs from wallet state into the local subscriptions table (`chia/data_layer/data_layer.py:979-985`), and `fetch_and_validate()` iterates over those server URLs and calls `insert_from_delta_file()` → `download_file()` → `http_download()`: [2](#0-1) [3](#0-2) 

`http_download()` performs `session.get(server_info.url + "/" + filename, ...)` with no validation of the URL's host — no check against loopback/private/link-local ranges, no scheme allow-list. This is a more severe version of the reported bug class: rather than an *incomplete* blacklist (as in `private_address_check` < 0.4.1), there is *no* blacklist at all on this attacker-reachable SSRF sink. `get_downloader()` similarly `session.post(d.url + "/handle_download", ...)` against configured plugin URLs, and `get_peer_info()` in `chia/server/server.py` calls fixed external HTTPS endpoints (not attacker-controlled, so not part of this analog).

For contrast, the codebase does implement private-address protections elsewhere — e.g. `AddressManager` rejects private subnets by default (`.cursor/context/server.md:70`), and `chia/util/network.py`'s `is_localhost`/`is_trusted_cidr`/`is_in_network` gate peer-connection trust — showing the project is aware of this bug class in other contexts but has not applied it to DataLayer mirror/plugin URLs.

### Impact Explanation
An unprivileged actor who can broadcast a standard spend bundle (creating a mirror coin with a crafted memo) can force any full node/DataLayer service that is subscribed to (tracking) the targeted launcher id to make outbound HTTP GET/POST requests to attacker-chosen hosts and ports — including `127.0.0.1`, RFC1918 ranges, and cloud metadata services (`169.254.169.254`). This can be used to:
- Probe/port-scan the operator's internal network from the node's vantage point.
- Reach internal-only services (e.g., local RPC ports, internal admin panels) that are not otherwise externally reachable, since the request originates from inside the trust boundary.
- Potentially reach the node's own local RPC endpoints on the same host if authentication is weak or if timing/response side channels leak information (blind SSRF).

This qualifies as unauthorized network access initiated from a trusted node context, directly caused by unauthenticated/unprivileged input (spend-bundle memo), matching the "concrete unsigned or unauthorized coin movement... or a spend-triggered transaction-processing halt" bar loosely via unauthorized SSRF-driven network reach — severity is High given the breadth of reachable internal targets and the trivial cost to the attacker (any wallet user, no ownership of the target store required).

### Likelihood Explanation
Likelihood is high: creating a mirror coin with an arbitrary launcher id/URL memo requires only a standard signed spend from the attacker's own coins (via `dl_new_mirror`/`create_new_mirror`), no special privileges, no cooperation from the store owner, and no consensus-level restriction on mirror memo contents. The only precondition is that the victim node must be tracking (subscribed to or dl_track_new'd) the targeted launcher id — a common state for any node participating in DataLayer sync for that store.

### Recommendation
- In `DataLayerWallet.coin_added()`, before persisting attacker-controlled mirror URLs, validate that the mirror coin's `launcher_id` is legitimately associated with the mirror creator (e.g., verify against known/owned launcher records) or at minimum sanitize/validate the URLs before they are ever used as network destinations.
- Add centralized SSRF protection (equivalent to a maintained private-address check, resolving DNS and rejecting loopback/link-local/private/multicast/reserved ranges, and restricting scheme to `http`/`https` with sane ports) in `download_data.http_download()`, `DataLayer.get_downloader()`, and any other place that issues outbound requests to mirror/plugin URLs (`chia/data_layer/download_data.py`, `chia/data_layer/data_layer.py`).
- Reuse/extend the existing `chia/util/network.py` helpers (`is_localhost`, `is_in_network`, `is_trusted_cidr`) or the `AddressManager` private-subnet policy pattern already used for peer discovery, applying the same policy to DataLayer mirror/plugin/download URLs, resolved at connection time (not just string-matching hostnames) to prevent DNS-rebinding bypasses.

### Proof of Concept
1. Attacker creates a store mirror coin via `dl_new_mirror` (`chia/wallet/wallet_rpc_api.py:3255-3274`) or the raw puzzle, setting `launcher_id` to a store id that the victim node is known to track/subscribe to, and `urls=["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8555/"]`.
2. Attacker pushes the spend bundle to the mempool; it needs no special permission and spends only the attacker's own coins.
3. Once confirmed, the victim's `DataLayerWallet.coin_added()` sees the mirror coin, checks only `is_launcher_tracked(launcher_id)` (true, since the victim tracks that store), and stores the attacker-chosen URLs as mirrors for that launcher id (`chia/data_layer/data_layer_wallet.py:775-800`).
4. On the victim's next `periodically_manage_data()` cycle, `update_subscriptions_from_wallet()` copies these URLs into the local subscription table, and `fetch_and_validate()` calls `http_download()`/`get_downloader()` against them (`chia/data_layer/data_layer.py:642-747`, `chia/data_layer/download_data.py:110-169,298-324`), issuing an outbound HTTP request from the victim node to the attacker-specified internal/loopback address with no filtering.

### Citations

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
