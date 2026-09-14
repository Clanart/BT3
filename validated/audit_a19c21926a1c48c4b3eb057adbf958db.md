## Analysis

The reported bug class is CWE-918 SSRF: a server-side component makes an outbound HTTP request to a URL that is fully attacker-controlled, with no restriction on scheme/host/port (bypassing a naive filter). The closest reachable analog in this codebase is the DataLayer mirror/subscription download path, where mirror URLs are published on-chain by any wallet that owns a DL singleton and are later fetched directly by any node that subscribes to that store, without any host/port/scheme validation.

### Title
Unrestricted outbound HTTP fetch to attacker-published mirror URLs enables SSRF in DataLayer sync - (File: chia/data_layer/download_data.py)

### Summary
Any unprivileged wallet owner can create a DataLayer singleton and call `dl_new_mirror` to publish arbitrary URL strings on-chain via `DataLayer.add_mirror()`, which only checks that the URL list is non-empty before pushing it to the chain.

### Finding Description
`add_mirror()` performs no validation on the `urls` list beyond non-emptiness before calling `wallet_rpc.dl_new_mirror`, so any string (including `http://127.0.0.1:<port>/...`, `http://169.254.169.254/...`, or references to other local services) is accepted and published as an on-chain mirror record. [1](#0-0) 

Any peer that subscribes to that store id periodically pulls these mirror URLs from wallet state via `update_subscriptions_from_wallet()`, which only trims trailing slashes and stores the URL verbatim. [2](#0-1) 

`fetch_and_validate()` then iterates the stored `servers_info`, randomly selecting one, and directly uses its `url` field to attempt a delta-file download via `insert_from_delta_file()`/`http_download()`, with no allowlist or SSRF filtering of host, port, or scheme. [3](#0-2) 

`http_download()` builds the request target by simple string concatenation, `server_info.url + "/" + filename`, and issues an `aiohttp` `GET` request straight to that URL with no restriction to public/external hosts. [4](#0-3) 

The module's own design notes acknowledge mirror URLs are external, untrusted inputs, but the stated mitigation is limited to validating *downloaded data* against the wallet-advertised root — not to validating the *destination* of the request before it is sent. [5](#0-4) 

Because the outbound request is fired before any content validation occurs, the SSRF side effect (network connection/HTTP request delivered to an attacker-chosen internal endpoint) happens regardless of whether the response later fails Merkle-root verification.

### Impact Explanation
An attacker who merely owns a DataLayer singleton (no special privilege — any wallet user can create one) can cause any node that subscribes to their store to issue unauthenticated outbound HTTP(S) requests to arbitrary internal or loopback-scoped addresses (e.g. internal RPC ports, cloud metadata endpoints, other services on the victim's LAN). This can be used for internal network reconnaissance/port scanning, and to trigger side effects on internal HTTP services that trust request origin (localhost-only APIs), and to attempt GET-based state changes on such internal endpoints. It does not directly leak response bodies back to the attacker (responses are only consumed locally as delta-file bytes), which caps severity below data-exfiltration SSRF, but request-triggering SSRF against internal services is still a meaningful security boundary violation for any node that runs the DataLayer service with mirror subscriptions enabled.

### Likelihood Explanation
Likelihood is High for exploitation of the request-forgery primitive itself: creating a DL singleton, publishing a mirror URL, and getting another node to subscribe (a normal, expected DataLayer workflow) are all standard, low-privilege operations, requiring no bypass of any existing filter because no filter exists at all.

### Recommendation
Validate and restrict mirror/server URLs before they are used to make outbound requests: enforce an allowed scheme list (http/https), resolve and reject requests to loopback, link-local, and private/RFC1918 address ranges (and re-check the resolved IP, not just the hostname, to avoid DNS-rebinding bypass), and optionally support a configurable allowlist/denylist similar to `is_trusted_peer()`/`is_in_network()` patterns already used elsewhere in the codebase. Apply this validation both when a mirror is stored via `update_subscriptions_from_wallet()` and immediately before `http_download()`/plugin `get_downloader()` requests are dispatched.

### Proof of Concept
1. Attacker wallet A creates a DataLayer store: `create_data_store`.
2. Attacker calls `add_mirror(store_id, urls=["http://127.0.0.1:<internal-port>/handle_download"], amount=1, fee=1)` (or a URL pointing at another local/internal service), which is accepted with no validation. [1](#0-0) 
3. Victim node subscribes to `store_id` (e.g., via `data subscribe -u`), pulling the mirror URL into its local subscription table unmodified. [2](#0-1) 
4. During the victim's periodic sync loop, `fetch_and_validate()` selects the malicious server and issues `GET http://127.0.0.1:<internal-port>/<filename>` via `http_download()`, exactly reaching whatever internal service listens on that host/port — regardless of whether the response is a valid delta file. [6](#0-5)

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

**File:** .cursor/context/data-layer.md (L87-88)
```markdown
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
