### Title
DataLayer mirror/plugin URL fetches follow HTTP redirects without re-validation, enabling SSRF-style requests to internal network resources reachable by a Data Layer client - (File: chia/data_layer/download_data.py)

### Summary
Chia's DataLayer subsystem lets any DataLayer wallet owner publish on-chain "mirror" coins that advertise a server URL for a `store_id`, and lets nodes subscribe to stores and download delta/full files from the advertised URLs. The download logic in `http_download()` uses `aiohttp.ClientSession().get(server_info.url + "/" + filename, ..., proxy=proxy_url)` without disabling or restricting redirects [1](#0-0) . `aiohttp` follows HTTP redirects by default when no `allow_redirects=False` is passed, and no redirect target validation exists anywhere in `download_data.py` or `data_layer.py` (confirmed no occurrences of `allow_redirects`/`max_redirects` in the codebase). This mirrors the lobe-chat SSRF-bypass pattern: an initial URL check (or, here, no check at all beyond accepting the operator/attacker-supplied mirror URL) is not re-applied to the redirect target, allowing the fetch to be steered to arbitrary internal/loopback resources.

### Finding Description
`DataLayer.fetch_and_validate()` iterates the locally-known `servers_info` for a subscribed `store_id`, obtained via `self.data_store.get_available_servers_for_store(store_id, timestamp)`, and calls `insert_from_delta_file(...)` with each `server_info.url` [2](#0-1) . That function calls `download_file()`, which — when no plugin `downloader` is configured — calls `http_download()` [3](#0-2) .

`http_download()` performs:
```python
async with session.get(
    server_info.url + "/" + filename,
    headers=headers,
    timeout=timeout,
    proxy=proxy_url,
) as resp:
``` [1](#0-0) 

There is no `allow_redirects=False`, and no post-redirect URL/IP validation. Because mirror server URLs are populated from on-chain mirror-coin data associated with a `store_id` (via the DataLayer wallet's mirror puzzle machinery in `chia/data_layer/data_layer_wallet.py`, using `MIRROR_PUZZLE_HASH`/`create_mirror_puzzle`) and any wallet holding the DataLayer wallet type can create/announce a mirror URL for a store it controls, a malicious store owner can advertise a URL that returns an HTTP redirect to an internal/loopback address (e.g., `http://127.0.0.1:<port>/...` or a cloud metadata endpoint). Any full node/Data Layer service that subscribes to that store id will transparently follow the redirect and issue a GET request to the internal target from within the node's own network context, echoing the exact bug class described in the report (initial URL/allowlist reasoning bypassed by not validating the redirect destination).

This is reachable purely by:
1. An attacker publishing a DataLayer store and mirror coin with a URL under their control (a normal, unprivileged DataLayer/wallet operation).
2. A victim node choosing (or being configured/automated) to subscribe to that store id and attempt to sync it, triggering `fetch_and_validate()` → `http_download()`.

### Impact Explanation
A successful redirect-based SSRF here allows probing/hitting internal-only HTTP services reachable from the victim's Data Layer node process (e.g., other local RPC ports, container-internal services, cloud metadata endpoints), which can leak internal information or be used as a pivot for further attacks. This does not directly enable coin theft or consensus divergence, but it is a network-boundary violation reachable from a routine, low-privilege Data Layer client action (subscribing to and syncing an untrusted store), matching the CWE-918 (SSRF) bug class in the report.

### Likelihood Explanation
Likelihood is moderate: it requires a victim to subscribe to an attacker-controlled store (a normal, expected DataLayer workflow of following stores/mirrors published by others), and requires the victim's DataLayer node to have network access to the intended internal target from wherever it runs (e.g., a docker/cloud deployment). No special privileges beyond being a Data Layer client are needed to mount the attack; only reachability to sensitive internal endpoints determines actual impact.

### Recommendation
In `http_download()` (`chia/data_layer/download_data.py`), disable automatic redirect following (`allow_redirects=False`) or, if redirects must be supported, manually follow each `Location` header and re-validate the resolved host/IP is not private/loopback/link-local before continuing to fetch, mirroring the fix pattern lobe-chat adopted (disable redirects or re-check on every hop).

### Proof of Concept
1. Deploy two Data Layer nodes, Attacker and Victim, on a network where Victim's node can also reach an internal-only HTTP service (e.g., `localhost:<port>` on the Victim's host/container).
2. Attacker creates a DataLayer store and adds a mirror coin whose URL points to an attacker-controlled HTTP server that responds with a 301/302 redirect to `http://127.0.0.1:<victim_internal_port>/<probe-path>`.
3. Victim subscribes to the attacker's `store_id` (standard `dl_track_new` / subscribe RPC flow), causing `DataLayer.fetch_and_validate()` to run and call `insert_from_delta_file()` → `download_file()` → `http_download()` against the attacker's mirror URL [4](#0-3) [5](#0-4) .
4. Because `aiohttp` follows the redirect by default, Victim's node issues a GET request to `127.0.0.1:<victim_internal_port>/<probe-path>`, confirmed by observing the request on the internal service, without the DataLayer code performing any check on the redirect destination.

Note: I was unable to fully confirm from the index whether every deployment/config path for mirror URL creation (`chia/wallet/wallet_rpc_api.py`, `chia/data_layer/dl_wallet_store.py`) applies any URL scheme/host restriction before storing mirror URLs; the search only confirmed the absence of `allow_redirects`/`max_redirects` usage and the direct `aiohttp.ClientSession.get(...)` call in `http_download()`. A full audit of `create_new_mirror`/`dl_add_mirror` input validation would further strengthen this finding but was not fully explorable within the available search depth.

### Citations

**File:** chia/data_layer/download_data.py (L131-145)
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
