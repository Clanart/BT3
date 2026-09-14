### Title
Unvalidated Outbound HTTP Fetch to Attacker-Controlled Mirror/Subscription URLs Enables SSRF in DataLayer (No Host Validation at All) - (File: `chia/data_layer/download_data.py`)

### Summary
DataLayer mirror coins let *any* wallet holder publish arbitrary URLs on-chain for *any* store id, and DataLayer nodes that subscribe to that store automatically fetch those URLs over plain HTTP with **zero validation** of the destination host — not even the weak, DNS-rebindable check that Rocket.Chat's `checkUrlForSsrf` attempted. This is the same bug class as the disclosed report (SSRF via unauthenticated attacker-controlled URL fetch) but strictly worse, since there is no mitigating check to bypass at all.

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a mirror coin carrying arbitrary `urls` in its memos with no ownership check tying the mirror to the caller and no validation of the URL contents: [1](#0-0) 

The service-level entry point `DataLayer.add_mirror()` only checks that the URL list is non-empty before pushing this transaction on-chain — no scheme/host restriction: [2](#0-1) 

Any DataLayer node then pulls these on-chain mirror URLs directly into its local subscription server list without any validation: [3](#0-2) 

`fetch_and_validate()` iterates the resulting `servers_info` (URLs sourced from on-chain mirrors) and passes them straight into `insert_from_delta_file()`: [4](#0-3) 

Which in turn calls `download_file()` → `http_download()`, performing a raw `aiohttp` GET against `server_info.url` with no check that the resolved host is not loopback, link-local, private, or a cloud metadata address: [5](#0-4) 

No `ip_address`/`is_private` check (the primitive that exists elsewhere in the codebase, e.g. `chia/util/ip_address.py`) is applied anywhere in this fetch path. This is functionally the same root cause as CVE-2024-39713/the DNS-rebinding bypass report — an unauthenticated/low-trust actor supplies a URL that a service later fetches — except here there isn't even a first-pass SSRF check to defeat via DNS rebinding; the fetch is unconditional.

### Impact Explanation
Any wallet user (no special privilege, just the ability to submit a spend for the mirror-coin puzzle, `create_mirror_puzzle()`) can plant on-chain mirror URLs for an arbitrary store id. Any node running the DataLayer service that subscribes to or syncs that store id (a common, low-trust action for querying shared/public data-layer stores) will automatically issue outbound HTTP requests to attacker-chosen hosts, including internal-network addresses and cloud metadata endpoints (e.g., `169.254.169.254`), and will download/act on the response content (parsed as delta/full-tree file data by `DataStore.insert_into_data_store_from_file`). This can be used for internal network reconnaissance/data exfiltration and, depending on deployment, potential credential leakage via metadata services — matching the impact class described in the external report.

### Likelihood Explanation
Adding a mirror is a normal, unauthenticated (from the DataLayer server's perspective) on-chain wallet action with no ownership binding to the target store id, and subscribing to arbitrary store ids is an expected DataLayer client workflow. No SSRF mitigation exists in the fetch path at all, so exploitation does not require anything as sophisticated as DNS rebinding — a single attacker-controlled URL pointing straight at an internal IP is sufficient.

### Recommendation
- Validate and restrict mirror/subscription URLs before storing/fetching: reject non-HTTPS URLs or, at minimum, resolve and reject requests to private/loopback/link-local/reserved IP ranges (reuse `chia/util/ip_address.py`'s `IPAddress.is_private`).
- Re-validate the resolved IP immediately before the `aiohttp` request in `http_download()` (and in `download_file()`'s plugin path) to close any TOCTOU/DNS-rebinding window, or pin the connection to the first-resolved IP.
- Consider requiring mirror coins to be created only by the store's own DataLayer wallet, or otherwise rate-limit/allowlist mirror URL hosts before nodes act on them.

### Proof of Concept
1. Attacker wallet calls `add_mirror` RPC (`chia/data_layer/data_layer_rpc_api.py` → `DataLayer.add_mirror`) for an arbitrary `store_id` (does not need to own the store) with `urls=["http://169.254.169.254/latest/meta-data/"]` or an internal LAN IP.
2. The mirror coin is confirmed on-chain; any node's `DataLayerWallet.coin_added()` picks it up and stores it via `dl_wallet_store.add_mirror` (`chia/data_layer/dl_wallet_store.py`).
3. Any DataLayer service that subscribes to `store_id` calls `update_subscriptions_from_wallet()`, pulling the malicious URL into its subscription list (`chia/data_layer/data_layer.py:979-985`).
4. On the next sync cycle, `fetch_and_validate()` → `insert_from_delta_file()` → `download_file()` → `http_download()` issues an unauthenticated outbound GET to the attacker-chosen internal/metadata URL with no host validation (`chia/data_layer/download_data.py:298-320`).

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
