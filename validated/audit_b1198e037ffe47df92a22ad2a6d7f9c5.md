This confirms the vulnerability chain: mirror URLs are attacker-controlled on-chain data (stored via memos on a mirror coin, e.g. `create_new_mirror()` at [1](#0-0) , decoded with zero validation in `coin_added()` via `get_mirror_info()` at [2](#0-1) ). Any node that has ever subscribed to that store id automatically pulls these URLs in `update_subscriptions_from_wallet()` at [3](#0-2)  with only `.rstrip("/")` normalization, no scheme allow-listing, and then `fetch_and_validate()`/`insert_from_delta_file()`/`http_download()` will `aiohttp.ClientSession().get(server_info.url + "/" + filename, ...)` at [4](#0-3) , entirely automatically in the background sync loop `periodically_manage_data()`/`update_subscription()` at [5](#0-4) .

### Title
DataLayer mirror-coin URLs are unvalidated and drive automatic outbound HTTP requests from every subscriber's node (SSRF) - ([File: chia/data_layer/data_layer.py])

### Summary
The gowitness CVE class is "attacker-controlled URL scheme/target is trusted and used to fetch a resource without validation." Chia's DataLayer has an analogous unvalidated-URL trust boundary: any wallet user can publish an on-chain "mirror" coin containing arbitrary URL strings for their own store id, and any other DataLayer participant who has ever subscribed to that store id will have their node automatically dereference those URLs via outbound HTTP requests with no scheme/host validation.

### Finding Description
`DataLayerWallet.create_new_mirror()` lets any wallet owner mint a mirror coin whose memos embed an arbitrary list of URL strings for a launcher id they control [1](#0-0) . When any node syncs and sees this coin, `coin_added()` decodes the URLs straight from the puzzle solution with `get_mirror_info()` and stores them verbatim in the wallet DB via `dl_store.add_mirror()`, with no scheme or host validation [2](#0-1) .

Any node (a DataLayer client) that calls the `subscribe` RPC for that store id, e.g. `chia data subscribe`, will cause the background service loop to periodically call `update_subscriptions_from_wallet()`, which fetches `dl_get_mirrors()` results and pushes every mirror URL — trimmed only by `rstrip("/")` — into the local subscription table as trusted server URLs [3](#0-2) .

`periodically_manage_data()`'s `update_subscription()` then unconditionally calls `fetch_and_validate()` for each tracked store, which iterates `get_available_servers_for_store()` results (i.e., these attacker-supplied URLs) and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which does `session.get(server_info.url + "/" + filename, ...)` using `aiohttp.ClientSession` [4](#0-3)  and [5](#0-4) . Nothing in this chain restricts the URL scheme, host, or port — it is not limited to public HTTP mirrors, and can point at internal-only or link-local addresses (e.g., cloud metadata endpoints, internal management ports) reachable from the victim node's network namespace.

This mirrors the CVE's bug class ("unauthenticated/attacker-controlled URL value is dereferenced by the tool without validating its scheme/target") generalized from a headless-browser file:// read to a Chia SSRF: an untrusted, on-chain, attacker-chosen string is dereferenced as a live network fetch target by every subscriber's DataLayer service, without any allow-listing.

### Impact Explanation
Any node that subscribes to a store operated by an attacker (a completely ordinary DataLayer client action, required for normal store replication) will have its DataLayer process make outbound HTTP(S) requests to arbitrary attacker-chosen hosts/ports on a recurring schedule (`manage_data_interval`) with no user interaction beyond the initial subscribe. This is server-side request forgery from the victim's own infrastructure, which can be used to probe/attack internal services, hit cloud instance-metadata endpoints, or exfiltrate response timing/behavior. It does not by itself corrupt consensus state (downloaded file contents are validated against wallet-advertised Merkle roots before being trusted as data), so the impact is scoped to the SSRF/network side-channel rather than double-spend or coin-set divergence.

### Likelihood Explanation
Likelihood is high for triggering (any store creator can add a mirror with any URL string, and subscribing is routine), but the security-relevant precondition — a victim voluntarily subscribing to an attacker's store — limits blast radius somewhat compared to a fully unauthenticated network-wide bug. Still, no confirmation, allow-list, or private-IP filtering exists anywhere in this path.

### Recommendation
Validate and restrict mirror/subscription URLs before persisting or fetching them: enforce an `http`/`https` scheme allow-list, reject loopback/link-local/private address targets (or require explicit opt-in/config for such targets), and apply this validation both when accepting `add_mirror`/`subscribe` RPC input and when pulling URLs from on-chain mirror coins in `update_subscriptions_from_wallet()`/`coin_added()`.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror`/`dl_new_mirror` with `urls=["http://169.254.169.254/latest/meta-data/"]` (or any internal target) [6](#0-5) .
2. Victim runs `chia data subscribe -id <attacker_store_id>` to replicate the store, a normal DataLayer client operation.
3. On the next `periodically_manage_data()` cycle, the victim's node calls `update_subscriptions_from_wallet()`, pulling the attacker's URL into its subscription table [3](#0-2) .
4. `fetch_and_validate()`/`http_download()` issues an outbound `aiohttp` GET request to the attacker-controlled URL/path from the victim's node [4](#0-3) , demonstrating SSRF triggered purely by routine DataLayer subscription with no URL validation.

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

**File:** chia/data_layer/data_layer.py (L1102-1116)
```python
    async def update_subscription(
        self,
        worker_id: int,
        job: Job[Subscription],
    ) -> None:
        subscription = job.input

        try:
            await self.update_subscriptions_from_wallet(subscription.store_id)
            await self.fetch_and_validate(subscription.store_id)
            await self.upload_files(subscription.store_id)
            await self.clean_old_full_tree_files(subscription.store_id)
        except Exception as e:
            self.log.error(f"Exception while fetching data: {type(e)} {e} {traceback.format_exc()}.")

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
