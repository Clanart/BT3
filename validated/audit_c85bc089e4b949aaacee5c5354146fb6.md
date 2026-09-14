### Title
SSRF via attacker-controlled DataLayer mirror/server URLs causing automated internal-network HTTP requests - ([File: chia/data_layer/download_data.py])

### Summary
`DataLayer.fetch_and_validate()` and the mirror/subscription sync loop feed `server_info.url` values into `http_download()`/`download_file()`, which issue outbound `aiohttp` GET/POST requests without validating scheme, host, or restricting to public IP ranges. Server URLs originate from on-chain mirror announcements for a DataLayer store (attacker-controlled data), so an attacker who publishes a store/mirror can cause a subscribing DataLayer node to make arbitrary outbound HTTP(S) requests, matching the CVE-2024-41305 SSRF bug class ("plugin/remote URL parameter used to make arbitrary requests").

### Finding Description
`DataLayer.fetch_and_validate()` reads server URLs for a store via `self.data_store.get_available_servers_for_store(store_id, timestamp)` and randomizes/tries each `server_info.url` [1](#0-0) , then calls `insert_from_delta_file(...)` with that `server_info` [2](#0-1) , which in turn calls `download_file()` [3](#0-2) .

`download_file()` either performs a direct HTTP GET using `http_download()` (`server_info.url + "/" + filename`) or, if a plugin downloader is configured, POSTs the raw URL string to the plugin's `/download` endpoint as JSON [4](#0-3) . `http_download()` performs the actual request with no scheme/host allow-listing: `session.get(server_info.url + "/" + filename, ...)` [5](#0-4) .

Additionally, `DataLayer.get_downloader()` POSTs `{"store_id":..., "url": url}` directly to a configured downloader plugin's `/handle_download` endpoint using the same untrusted `url` [6](#0-5) .

These server/mirror URLs are populated from on-chain mirror coin data for a DataLayer singleton store — i.e., they are attacker-controlled values that any store owner can publish (via `DLNewMirror`), and any node that subscribes to (or is a mirror discovery target for) that store id will have them pulled into its local subscription server-info table and then automatically dereferenced by the periodic `periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()` loop described in the module context notes ("`fetch_and_validate()` ... randomizes eligible server URLs, tries plugin-specific or plain HTTP delta downloads") [7](#0-6) . The same context doc explicitly flags this class of code as an external trust boundary: "Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict" [8](#0-7)  — but this note is only about *data* integrity (Merkle root verification), not about restricting *where* the HTTP request itself may be sent, which is the SSRF gap.

No code path checks that `server_info.url`/`url` resolves to a public, non-internal address, restricts scheme to `http`/`https`, or blocks loopback/link-local/RFC1918 ranges before issuing the request.

### Impact Explanation
A DataLayer node that subscribes to (or discovers mirrors for) an attacker-created store id will automatically issue outbound HTTP/HTTPS requests to a URL fully chosen by the attacker (e.g. `http://127.0.0.1:<internal-rpc-port>/`, `http://169.254.169.254/latest/meta-data/...` on cloud hosts, or internal-network services). This can be used to:
- Probe/attack services on the node's local network or localhost (internal RPC ports, other services bound to loopback),
- Exfiltrate cloud instance metadata/credentials on cloud-hosted DataLayer nodes,
- Trigger the same URL to be relayed verbatim to a configured downloader plugin's HTTP API (`/handle_download`, `/download`), extending the blast radius to whatever internal trust the plugin service has (e.g., cloud storage credentials in `s3_plugin_service.py`).

This does not directly move funds or forge chain state, but it is a genuine SSRF against infrastructure that a "Data Layer client" (an operator subscribing to third-party stores) is exposed to purely by subscribing to an attacker's public store — consistent with the "Data Layer roots and proofs" reachable surface allowed by scope.

### Likelihood Explanation
Likelihood is moderate: exploitation only requires an attacker to publish a DataLayer store with a malicious mirror URL and get any other operator to subscribe to it (a normal, expected DataLayer workflow — subscribing to other people's stores is a core use case). No special privilege beyond being a DataLayer participant/subscriber is needed on the victim side, and no signature or on-chain consensus check gates what string is accepted as a mirror URL before automatic background fetch begins.

### Recommendation
Before dereferencing any `server_info.url` / mirror/plugin URL in `download_data.py` or `data_layer.py`, validate: enforce `http`/`https` scheme only, resolve and reject requests to loopback, link-local (including `169.254.169.254`), and private/reserved IP ranges (unless explicitly operator-allow-listed), and apply the same validation to URLs forwarded to downloader/uploader plugin HTTP calls (`get_downloader()`, `download_file()`'s plugin branch). Consider making outbound egress restrictions configurable and enabled by default for DataLayer service processes.

### Proof of Concept
1. Operator A creates a DataLayer store and adds a mirror pointing to an internal address reachable from other DataLayer nodes' hosts, e.g. via the wallet RPC flow backing `DLNewMirror`/mirror publication (`chia/wallet/wallet_request_types.py` `DLNewMirror`) with `urls=["http://127.0.0.1:8555/some-admin-endpoint", "http://169.254.169.254/latest/meta-data/iam/security-credentials/"]`.
2. Operator B subscribes to store A's `store_id` (normal DataLayer subscription flow).
3. Operator B's `DataLayer.periodically_manage_data()` loop eventually calls `fetch_and_validate(store_id)`, which enumerates `get_available_servers_for_store()` including the attacker URLs and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, issuing an outbound GET to the attacker-chosen internal/loopback/metadata URL [9](#0-8) [5](#0-4) .
4. If B has a downloader plugin configured, the raw URL is also POSTed verbatim to the plugin's `/handle_download` and `/download` endpoints [6](#0-5) [10](#0-9) , extending SSRF reach to the plugin's own request handling.

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

**File:** chia/data_layer/download_data.py (L131-165)
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

    log.info(f"Using downloader {downloader} for store {store_id.hex()}.")
    request_json = {
        "url": server_info.url,
        "client_folder": str(client_foldername),
        "filename": filename,
        "group_files_by_store": group_downloaded_files_by_store,
        "max_delta_file_size": max_delta_file_size,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                downloader.url + "/download",
                json=request_json,
                headers=downloader.headers,
                timeout=timeout,
            ) as response:
                res_json = await response.json()
                assert isinstance(res_json["downloaded"], bool)
                return res_json["downloaded"]
```

**File:** chia/data_layer/download_data.py (L200-216)
```python
            success = await download_file(
                data_store=data_store,
                target_filename_path=target_filename_path,
                store_id=store_id,
                root_hash=root_hash,
                generation=existing_generation,
                server_info=server_info,
                proxy_url=proxy_url,
                downloader=downloader,
                timeout=timeout,
                client_foldername=client_foldername,
                timestamp=timestamp,
                log=log,
                grouped_by_store=grouped_by_store,
                group_downloaded_files_by_store=group_files_by_store,
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

**File:** .cursor/context/data-layer.md (L63-63)
```markdown
- `fetch_and_validate()` uses wallet singleton history as the target generation/root sequence, randomizes eligible server URLs, tries plugin-specific or plain HTTP delta downloads, and marks mirror/server failures with backoff in the subscriptions table.
```

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
