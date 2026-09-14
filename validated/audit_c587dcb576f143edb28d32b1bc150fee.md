### Title
Data Layer subscriber SSRF via attacker-controlled mirror/server URLs in `fetch_and_validate` / `http_download` - (File: chia/data_layer/download_data.py)

### Summary
Chia's Data Layer stores mirror URLs on-chain (published by any store owner via `add_mirror`/`DLNewMirror`), and any node that subscribes to that store's singleton will automatically fetch delta/full-tree files from those URLs with no scheme, host, or IP-range validation, mirroring the Nautobot Webhook SSRF bug class (CWE-918): user-configurable destination URLs are used to make outbound HTTP requests without any allow/deny-list of network destinations.

### Finding Description
Any Data Layer store owner can call `add_mirror`, which pushes a `DLNewMirror` spend that records arbitrary `urls` on-chain via the wallet's DL mirror puzzle [1](#0-0) . These URLs are later read back by any node that subscribes to the store: `update_subscriptions_from_wallet` pulls `mirrors[].urls` from `dl_get_mirrors` and stores them verbatim in the subscriptions table with no validation [2](#0-1) .

During normal sync, `fetch_and_validate` iterates `get_available_servers_for_store()` (populated from those attacker-supplied URLs) and calls `insert_from_delta_file`, which in turn calls `download_file` → `http_download` [3](#0-2) . `http_download` performs an unrestricted `aiohttp` GET directly against `server_info.url + "/" + filename` with no scheme allow-list and no check against private/link-local/loopback/metadata IP ranges (e.g. `169.254.169.254`, RFC1918 addresses, `localhost`) [4](#0-3) . The plugin-downloader path (`get_downloader`) similarly POSTs the attacker-controlled `url` to configured plugin remotes as part of the request body without validating the target [5](#0-4) , and `download_file`'s non-plugin branch forwards `server_info.url` unchanged [6](#0-5) .

This is structurally identical to the Nautobot Webhook SSRF: a data model (`Webhook`/DL `Mirror`) stores a user-supplied destination URL, and a background process later issues an HTTP request to that URL with no destination restriction, allowing the URL owner to direct the victim's server to make requests to internal-only endpoints.

### Impact Explanation
An unprivileged Data Layer client that subscribes to (or is induced to subscribe to) a malicious store operator's singleton will have its local `data_layer` service, running with server-side network access, issue outbound HTTP(S) requests to attacker-chosen hosts (e.g. internal RFC1918 services, cloud metadata endpoints, or other locally reachable services) once per sync interval. Response bodies are parsed as delta-file/JSON content, and error/timing behavior is observable by the attacker (e.g., via `server_misses_file`/backoff state and log timing), giving a limited request/response oracle against internal infrastructure — consistent with CVSS AC:L / PR:L / C:H style SSRF impact, without requiring any wallet key compromise.

### Likelihood Explanation
Likelihood is high for the "victim subscribes to attacker's store" path: subscribing to a Data Layer store is a normal, expected user action (`dl_track_new`/`subscribe` RPC), and mirror URLs are attached to the store's own on-chain history, so any store the victim subscribes to can carry attacker-controlled URLs. The mirror-creation side additionally requires the attacker to own/control a singleton and spend a small amount to add mirror coins, which is a low-cost, permissionless on-chain action available to any wallet holder.

### Recommendation
Validate and restrict all Data Layer mirror/server URLs before use for outbound requests: enforce an allow-list of schemes (http/https only), resolve and reject requests targeting private, loopback, link-local, and cloud metadata IP ranges (analogous to Nautobot's new `WEBHOOK_ALLOWED_SCHEMES` / `WEBHOOK_ADDITIONAL_BLOCKED_NETWORKS` settings), and apply this validation both when storing mirror URLs (`update_subscriptions_from_wallet`) and immediately before each request in `http_download` and `get_downloader`.

### Proof of Concept
1. Attacker creates a Data Layer store and calls `add_mirror(store_id, urls=["http://169.254.169.254"], amount, fee)`, which is accepted on-chain with no URL validation [1](#0-0) .
2. Victim runs `chia data subscribe` (or equivalent RPC) for that `store_id`; the victim's `update_subscriptions_from_wallet` pulls the mirror URL into local subscriptions unmodified [2](#0-1) .
3. On the victim's next sync cycle, `fetch_and_validate` selects the malicious server and `http_download` issues a GET to `http://169.254.169.254/<filename>` from the victim's Data Layer process [4](#0-3) .
4. Response status/timing/content (parsed as a delta file) leaks information about the internal/metadata endpoint back to the attacker via subsequent on-chain interactions or observable retry/backoff behavior.

### Citations

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

**File:** chia/data_layer/download_data.py (L130-145)
```python

    if downloader is None:
        # use http downloader - this raises on any error
        try:
            await http_download(
                target_filename_path, filename, proxy_url, server_info, timeout, log, max_delta_file_size
            )
        except (asyncio.TimeoutError, aiohttp.ClientError, MaxDeltaFileSizeExceededError):
            new_server_info = await data_store.server_misses_file(store_id, server_info, timestamp)
            log.info(
                f"Failed to download {filename} from {new_server_info.url}."
                f"Miss {new_server_info.num_consecutive_failures}."
            )
            log.info(f"Next attempt from {new_server_info.url} in {new_server_info.ignore_till - timestamp}s.")
            return False
        return True
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
