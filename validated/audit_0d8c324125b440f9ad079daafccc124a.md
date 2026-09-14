I have enough evidence to write the finding now.

### Title
DataLayer mirror URLs published by any wallet are auto-followed by subscribed nodes, causing unauthenticated Server-Side Request Forgery (SSRF) - ([File: chia/data_layer/data_layer_wallet.py])

### Summary
Chia's DataLayer subscription/mirror mechanism lets any wallet holder publish an on-chain "mirror" coin containing an arbitrary URL for *any* DataLayer `launcher_id` — not just stores they own — by spending a small amount of XCH. Any node that is already subscribed/tracking that store id will pick up the attacker's URL and periodically issue outbound HTTP GET requests to it via `http_download()`, with no validation of scheme, host, or destination (no SSRF protections, no allow-list, default redirect following). This mirrors the CVE-2026-1180 bug class exactly: an unprivileged/unauthenticated party supplies an arbitrary URI that the server unconditionally fetches, enabling internal network probing (metadata endpoints, internal admin/RPC services, etc.).

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a mirror coin whose memo encodes `[launcher_id, *urls]` via a standard XCH spend, with **no check that the caller owns or controls that `launcher_id`**: [1](#0-0) 

When this mirror coin is later seen on-chain, `DataLayerWallet.coin_added()` decodes the launcher id and URLs and stores them in the local `WalletPoolStore`/DL store **as long as the node is already tracking that launcher id** — there is no check on who created the mirror coin or whether the URLs are safe: [2](#0-1) 

The `DataLayer` service's background loop pulls these wallet-tracked mirrors and blindly merges their URLs into the local subscription server list for that store: [3](#0-2) 

`fetch_and_validate()` then shuffles and iterates these server URLs, calling `insert_from_delta_file()` → `download_file()` → `http_download()`: [4](#0-3) 

`http_download()` performs an unauthenticated, unvalidated `aiohttp` GET request against `server_info.url + "/" + filename`, with no scheme/host allow-listing and default redirect-following behavior: [5](#0-4) 

Because any wallet (an unprivileged, unauthenticated-by-the-target actor) can create a mirror coin for a store id that a victim node already subscribes to (subscribing to a public store is a normal, expected DataLayer client action), the attacker fully controls the destination the victim's DataLayer service will repeatedly request from — the textbook CWE-918 pattern from the report (Keycloak's client-supplied `jwks_uri` fetched without destination validation).

### Impact Explanation
A victim node's DataLayer service can be coerced into issuing repeated, timed HTTP requests to attacker-chosen internal or restricted network destinations (cloud metadata services, internal-only HTTP APIs, other local services bound to loopback/private interfaces), enabling network reconnaissance and, depending on response handling, information disclosure through timing/error side channels (e.g., the "ban" backoff behavior in `download_file`/`server_misses_file` leaks whether a connection attempt succeeded or failed). This does not directly forge coin spends or corrupt consensus state, but it is a confidentiality/reconnaissance risk against the operator's local network, matching the Medium severity of the original Keycloak advisory.

### Likelihood Explanation
Likelihood is fairly high for any operator running a DataLayer node that subscribes to third-party/public stores (a normal, encouraged DataLayer usage pattern): any user can pay a trivial fee to add a mirror URL for a `launcher_id` that a victim is already subscribed to, since `create_new_mirror`/`dl_new_mirror` performs no ownership check on the launcher id. The periodic sync loop (`update_subscription()`/`periodically_manage_data()`) automatically retries the malicious URL without operator interaction.

### Recommendation
- Restrict outbound mirror/subscription fetches to an explicit host/scheme allow-list, or require operator opt-in confirmation before a wallet-discovered mirror URL (not explicitly subscribed to by the operator) is added to `get_available_servers_for_store()`.
- In `http_download()`/`download_file()`, reject non-HTTPS schemes, disable automatic redirect following (or re-validate the redirect target), and block requests to loopback/link-local/private IP ranges (e.g. `169.254.169.254`, RFC1918) unless explicitly permitted by config.
- Consider requiring mirror coins to prove some relationship to the singleton (e.g., only accept "ours" mirrors, or only accept externally-discovered mirrors after explicit operator approval via RPC) rather than passively trusting on-chain mirror memos from arbitrary payers.

### Proof of Concept
1. Attacker identifies a DataLayer `store_id`/`launcher_id` that a victim node is known to be subscribed to (subscriptions are visible via `get_subscriptions`/public DataLayer usage).
2. Attacker calls `dl_new_mirror` (`chia/data_layer/data_layer_wallet.py:687`) via their own wallet with `launcher_id=<victim's tracked store>` and `urls=["http://169.254.169.254/latest/meta-data/", ...]` (or any internal-network URL), paying a minimal fee — no ownership check is enforced.
3. Once the mirror coin confirms, the victim's `DataLayerWallet.coin_added()` (`chia/data_layer/data_layer_wallet.py:775`) records the attacker's URL because the launcher id is already tracked.
4. The victim's `update_subscriptions_from_wallet()` (`chia/data_layer/data_layer.py:979`) merges the URL into the local subscription server list.
5. On the next sync cycle, `fetch_and_validate()`/`http_download()` (`chia/data_layer/data_layer.py:642`, `chia/data_layer/download_data.py:298`) issues an outbound HTTP GET to the attacker-controlled destination automatically and repeatedly, confirming SSRF.

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

**File:** chia/data_layer/download_data.py (L298-320)
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
```
