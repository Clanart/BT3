### Title
Unauthenticated SSRF via DataLayer mirror-coin URLs fetched without validation or redirect protection - (File: `chia/data_layer/download_data.py`)

### Summary
`chia/data_layer/download_data.py:http_download()` fetches attacker-supplied mirror URLs with `aiohttp.ClientSession().get(server_info.url + "/" + filename, ...)` and performs **no destination validation whatsoever** — no scheme/host allow-list, no private/loopback/link-local IP blocking, and no re-validation of redirect targets (aiohttp follows redirects by default). This is the same bug class as GHSA-fv5p-p927-qmxr (SSRF via unvalidated fetch/redirect), except here there isn't even an initial `validate_safe_url()`-style check — any URL string, including ones a completely unrelated third party can inject on-chain, is dereferenced directly by the node's HTTP client.

### Finding Description
Any wallet can create a "mirror" coin for **any** DataLayer store id — including stores it does not own — by spending to `create_mirror_puzzle().get_tree_hash()` with memos `[[launcher_id, *urls]]`, via `DataLayerWallet.create_new_mirror()` [1](#0-0) . This coin creation is a spend anyone can submit; ownership of `launcher_id` is never checked.

When a victim node has previously subscribed to (is "tracking") that `launcher_id`, `DataLayerWallet.coin_added()` picks up the mirror coin and stores the attacker-chosen URL list unconditionally: [2](#0-1) 

`DataLayer.update_subscriptions_from_wallet()` then copies these URLs straight into the local subscription server list with no validation, only a trailing-slash strip: [3](#0-2) 

During the periodic sync loop, `DataLayer.fetch_and_validate()` selects one of these server URLs and calls `insert_from_delta_file()` → `download_file()` → `http_download()`: [4](#0-3) [5](#0-4) 

`http_download()` performs a raw `session.get(server_info.url + "/" + filename, ...)` with default aiohttp redirect-following and no SSRF guard of any kind — it does not check for private IPs, loopback, link-local metadata addresses (e.g. `169.254.169.254`), or validate any redirect Location header. This is strictly weaker than the patched `langchain-text-splitters` code, which at least called `validate_safe_url()` once before the fetch; here there is no equivalent check at all.

### Impact Explanation
Any unprivileged spend-bundle submitter can force a victim DataLayer node — merely because it subscribes to/tracks a given store id, a normal and expected client action — to issue outbound HTTP GET requests to attacker-chosen hosts, including internal network services or cloud metadata endpoints (e.g. AWS IMDSv1, which needs no special headers and is reachable via a bare `GET`). Because the attacker fully controls the URL (not just a redirect target), this is a more complete SSRF primitive than the CVE it mirrors. Depending on deployment, this can be used to probe/interact with internal admin services reachable from the DataLayer host, or to pull data from unauthenticated internal HTTP endpoints into the node's filesystem (subject to `insert_into_data_store_from_file`'s hash-based validation for actual DL data acceptance, but the network request itself, timing, and error side channels are already exposed regardless of validation outcome).

### Likelihood Explanation
Likelihood is moderate-to-high for any Data Layer client that subscribes to third-party stores (a normal usage pattern for shared/public DL data). No special privilege is needed to create a mirror coin for an arbitrary `launcher_id` — the puzzle and RPC (`dl_new_mirror`) do not check ownership. The only precondition is that the victim already tracks that `launcher_id`, which is exactly the scenario mirrors exist to serve (helping subscribers discover download servers).

### Recommendation
- Before making a network request in `http_download()` (and `get_downloader()`/`get_uploaders()`/`check_plugins()` plugin URL fetches), validate the resolved destination against a private/loopback/link-local/multicast address block-list, and re-validate on every redirect hop (or disable automatic redirects and re-check the `Location` header manually before following).
- Consider requiring an explicit user opt-in / allow-list for mirror URLs rather than auto-adopting on-chain-announced mirror coins for tracked stores, since ownership of `launcher_id` is not required to publish a mirror.
- Apply the same validation to `DataLayerServer`'s downloader/uploader plugin URL handling where SSRF-relevant fetches occur.

### Proof of Concept
1. Attacker (any funded wallet, no relationship to `launcher_id` of victim's tracked store) spends a coin to `create_mirror_puzzle().get_tree_hash()` with `memos=[[launcher_id, "http://169.254.169.254/latest/meta-data/"]]` via `dl_new_mirror`/`create_new_mirror` — this is a plain, unauthenticated spend bundle.
2. Victim's `DataLayerWallet.coin_added()` observes the mirror coin for the tracked `launcher_id` and stores the URL unmodified in `dl_store` (chia/data_layer/data_layer_wallet.py:775-800).
3. Victim's `DataLayer.update_subscriptions_from_wallet()` copies the URL into `data_store` subscriptions (chia/data_layer/data_layer.py:979-985).
4. On the next `fetch_and_validate()` cycle, `http_download()` issues `GET http://169.254.169.254/latest/meta-data/<filename>` from the victim's node with no destination checks (chia/data_layer/download_data.py:298-319), completing the SSRF.

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
