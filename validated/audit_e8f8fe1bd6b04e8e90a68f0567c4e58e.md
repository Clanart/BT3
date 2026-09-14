### Title
SSRF via untrusted DataLayer mirror/plugin URLs fetched during subscription sync - (File: chia/data_layer/download_data.py)

### Summary
DataLayer's subscription-sync path (`fetch_and_validate` → `insert_from_delta_file` → `download_file` → `http_download`) issues outbound HTTP requests to server URLs that are sourced entirely from on-chain mirror coins created by arbitrary, unauthenticated third parties, with no validation of scheme/host/IP range before the local node's `aiohttp` client connects to them. This is the same bug class as the `alltube` SSRF (CVE-2022-0768): a server-side component makes an outbound request to an attacker-supplied URL without restricting internal/private destinations.

### Finding Description
Any wallet user can create a DataLayer singleton and attach mirror coins with attacker-chosen `urls` via `add_mirror()` [1](#0-0) . These URLs are stored on-chain in mirror coin memos and are later read back by any other node that subscribes to that store: `update_subscriptions_from_wallet()` pulls `mirror.urls` straight from `wallet_rpc.dl_get_mirrors()` and persists them as subscription server URLs with no filtering [2](#0-1) .

During the periodic sync loop, `fetch_and_validate()` randomly selects one of these attacker-controlled `server_info.url` values and passes it into `insert_from_delta_file()` / `download_file()` [3](#0-2) . When no plugin downloader is configured, `download_file()` calls `http_download()`, which opens an `aiohttp.ClientSession` and issues `session.get(server_info.url + "/" + filename, ...)` directly against the attacker-chosen URL/host, with no allowlist, no scheme restriction, and no check against private/loopback/link-local address ranges (e.g. `127.0.0.1`, `169.254.169.254`, internal service hostnames) [4](#0-3) .

The only "validation" applied to downloaded content is that the response body must parse as a valid DataLayer delta file feeding into `insert_into_data_store_from_file()` [5](#0-4) ; the request itself — including the destination host/port and any response headers/behavior observable via timing or error branches (`ClientConnectorError` vs. generic exception vs. timeout, all logged distinctly) — happens before any content validation, giving an attacker a blind SSRF probe against the victim's internal network (this project's `.cursor` documentation for the module explicitly flags mirror/plugin URLs as "external trust inputs" but only calls for validating downloaded *data*, not the destination [6](#0-5) ).

A similar unvalidated outbound POST occurs in `get_downloader()`, which posts JSON containing the mirror URL to each configured plugin's `/handle_download` endpoint, and in `get_plugin_info()` — though these are operator-configured plugin endpoints rather than attacker-supplied targets, so they are lower-severity variants of the same missing-validation pattern [7](#0-6) .

### Impact Explanation
Any Chia node running DataLayer and subscribing to a third-party store (a normal, expected DataLayer usage pattern — subscribing to public/shared stores is the core feature) can be made to issue outbound HTTP requests to arbitrary internal hosts/ports chosen by the store's operator, entirely without operator interaction beyond subscribing. This can be used to:
- Probe/reach internal services, cloud metadata endpoints, or other hosts not otherwise internet-reachable from the victim's network.
- Exfiltrate information via timing/error-based side channels (distinct log/error paths for connection-refused vs. timeout vs. generic exception let an attacker fingerprint internal network topology).
- Potentially pivot to internal HTTP services depending on network placement of the DataLayer node.

This does not directly move funds or corrupt consensus state, but it is a genuine unauthenticated SSRF reachable purely by an unprivileged Data Layer client action (subscribing to an attacker's public store), matching the CWE-918 class and severity category of the reference advisory.

### Likelihood Explanation
Likelihood is high for any node operator who subscribes to third-party DataLayer stores (a supported, encouraged workflow — e.g. following a publicly shared store). The attacker only needs to publish a DataLayer singleton with a mirror coin containing a malicious URL and get any victim to subscribe; the periodic `periodically_manage_data()` loop then automatically triggers the SSRF request without further victim action.

### Recommendation
- Before performing `http_download()`/plugin POST requests, resolve and validate the mirror/plugin URL's host against a denylist of private/loopback/link-local/reserved IP ranges (RFC 1918, 127.0.0.0/8, 169.254.0.0/16, etc.), and re-validate after DNS resolution to prevent DNS-rebinding bypass.
- Restrict allowed URL schemes to `http`/`https` only, and consider requiring an explicit operator allowlist of trusted mirror hosts rather than trusting arbitrary on-chain mirror URLs by default.
- Apply the same destination validation to `get_downloader()`'s plugin POST calls and `get_plugin_info()`.
- Document that mirror URLs are attacker-controlled untrusted input at the *destination* level, not just the *content* level, and enforce that at the network call site.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror(store_id, urls=["http://169.254.169.254/latest/meta-data/"], amount, fee)`, publishing the mirror coin on-chain [1](#0-0) .
2. Victim node subscribes to attacker's `store_id` (normal DataLayer usage) and its `update_subscriptions_from_wallet()` cycle picks up the malicious mirror URL as a subscription server [2](#0-1) .
3. On the next `periodically_manage_data()` cycle, `fetch_and_validate()` selects this server and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, causing the victim's node to issue `GET http://169.254.169.254/latest/meta-data/<delta-filename>` [4](#0-3) .
4. The victim's node has now made an unauthenticated outbound request to an internal/cloud-metadata address chosen entirely by the attacker, with response-handling differences (timeout, connection error, generic exception) observable indirectly via subsequent mirror-ban/backoff behavior, enabling blind SSRF probing.

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

**File:** chia/data_layer/data_layer.py (L731-747)
```python
    async def get_downloader(self, store_id: bytes32, url: str) -> PluginRemote | None:
        request_json = {"store_id": store_id.hex(), "url": url}
        for d in self.downloaders:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(
                        d.url + "/handle_download",
                        json=request_json,
                        headers=d.headers,
                        timeout=self.client_timeout,
                    ) as response:
                        res_json = await response.json()
                        if res_json["handle_download"]:
                            return d
                except Exception as e:
                    self.log.error(f"get_downloader could not get response: {type(e).__name__}: {e}")
        return None
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

**File:** chia/data_layer/download_data.py (L223-238)
```python
        try:
            with log_exceptions(log=log, message="exception while inserting from delta file"):
                filename_full_tree = get_full_tree_filename_path(
                    client_foldername,
                    store_id,
                    root_hash,
                    existing_generation,
                    group_files_by_store,
                )
                delta_reader = await data_store.insert_into_data_store_from_file(
                    store_id,
                    None if root_hash == bytes32.zeros else root_hash,
                    target_filename_path,
                    delta_reader=delta_reader,
                    max_delta_file_size=max_delta_file_size,
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

**File:** .cursor/context/data-layer.md (L87-89)
```markdown
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
- DataLayer wallet code depends on singleton CLVM structure, odd singleton amounts, lineage proofs, and offer solver field names. Changes in wallet puzzle drivers or offer summaries can break this module without direct edits here.
```
