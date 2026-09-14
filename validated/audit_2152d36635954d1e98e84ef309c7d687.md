### Title
DataLayer mirror URLs are attacker-controlled and unvalidated, enabling SSRF against any subscribing Data Layer client - (File: chia/data_layer/download_data.py)

### Summary
Any wallet user can create a DataLayer mirror record with arbitrary URL strings, memoed on-chain via a standard coin spend. Any other Data Layer node that subscribes to that store (a normal, expected client action) will have its local `DataLayer` service issue outbound HTTP GET requests to those attacker-supplied URLs with no host/scheme validation, exactly mirroring the Craft CMS bug class: attacker-controlled input flows unvalidated into a server-initiated HTTP request.

### Finding Description
`DLNewMirror`/`create_new_mirror` lets any wallet owner create a mirror coin whose memo contains an arbitrary list of URL strings, with no validation of scheme or host: [1](#0-0) [2](#0-1) 

These URLs are read back via `dl_get_mirrors`/`get_mirrors` and fed into `update_subscriptions_from_wallet()`, which stores them as server URLs for the store id with only a trailing-slash strip — no scheme or IP restriction: [3](#0-2) 

When a Data Layer node subscribes to that store and calls `fetch_and_validate()`, it selects one of these `ServerInfo.url` values and passes it into `insert_from_delta_file()` → `download_file()` → `http_download()`, which performs an unauthenticated, unrestricted `aiohttp` GET to `server_info.url + "/" + filename`: [4](#0-3) [5](#0-4) 

There is no validation anywhere in the mirror-creation, mirror-retrieval, or download path that restricts the URL to a public/expected host, blocks private/loopback/link-local addresses (e.g., `127.0.0.1`, internal service ports, cloud metadata IPs like `169.254.169.254`), or restricts the scheme. The project's own internal notes acknowledge this as a known trust boundary ("mirror URLs... are never trust anchors") but that statement is about *data* integrity (the downloaded content is re-verified against the wallet root), not about the *request itself* being safe to issue — the SSRF-relevant outbound request happens regardless of whether the returned data later fails validation.

### Impact Explanation
Any local Data Layer node that subscribes to a store controlled by an attacker (or any store where the attacker manages to insert a mirror pointing at itself) can be coerced into issuing arbitrary outbound HTTP GET requests. Because Data Layer typically runs alongside a wallet node on the same host/network, this can be used to probe or interact with internal-only services (local RPC ports, internal admin interfaces, cloud metadata endpoints) from the victim's machine, using it as a confused-deputy for network reconnaissance or triggering side effects on reachable internal HTTP endpoints. This matches the CWE-918 SSRF bug class in the reference report: attacker-controlled input (mirror URL) is trusted to build a server-issued request with no host restriction.

### Likelihood Explanation
Likelihood is moderate: creating a mirror with a malicious URL requires only a standard wallet transaction (no special privilege) via `dl_new_mirror`, and victim exposure requires only that the victim's Data Layer node subscribes to the attacker's store id and periodically runs `fetch_and_validate()` (this happens automatically for any subscribed store, per `periodically_manage_data()`). No user interaction beyond normal DL subscription (a supported/expected client workflow) is needed.

### Recommendation
- Validate mirror URLs at creation (`add_mirror`/`create_new_mirror`) and/or at subscription-fetch time (`update_subscriptions_from_wallet`, `http_download`) to reject non-`https`/`http` schemes, and reject or flag hosts that resolve to loopback, link-local, private, or otherwise non-routable/internal IP ranges unless explicitly allowed by local configuration.
- Consider requiring DNS resolution + IP-range checks immediately before each outbound fetch (to also defend against DNS-rebinding between validation time and request time), similar to standard SSRF-mitigation patterns.
- Document and, ideally, enforce this at the `ServerInfo`/`Mirror` construction boundary so all consumers (HTTP downloader and any plugin dispatch in `get_downloader`) share one hardened validation path.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `dl_new_mirror` (via wallet RPC) with `urls=["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8555/"]` — accepted without validation: [6](#0-5) 
2. Victim's Data Layer node subscribes to attacker's `store_id` (normal DL client action) and later calls `periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()`.
3. `fetch_and_validate()` picks the malicious `server_info.url` and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which issues `session.get("http://127.0.0.1:8555/<filename>", ...)` from the victim host: [7](#0-6) 
4. The victim's DataLayer process makes the outbound request to the internal/loopback address chosen by the attacker, confirming SSRF (observable via OOB listener or internal service side effects), independent of whether the returned "delta file" content later fails DataLayer's Merkle-root validation.

**Uncertainty note:** I was unable to fully confirm whether the same lack of validation applies identically to the plugin/downloader dispatch path (`get_downloader`, which POSTs the same attacker URL to configured plugin services) since plugin usage requires operator-configured `downloaders`/`uploaders`; the primary, always-reachable path is the direct `http_download()` route analyzed above, which requires no special operator configuration.

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

**File:** chia/wallet/wallet_request_types.py (L1853-1859)
```python
@streamable
@dataclass(frozen=True, kw_only=True)
class DLNewMirror(TransactionEndpointRequest):
    launcher_id: bytes32
    amount: uint64
    urls: list[str]

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
