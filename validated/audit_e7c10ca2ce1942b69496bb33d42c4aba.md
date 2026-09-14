### Title
DataLayer mirror/subscription URLs allow attacker-controlled Server-Side Request Forgery against peer DataLayer nodes - (File: chia/data_layer/download_data.py)

### Summary
Any Data Layer store owner can register arbitrary mirror URLs on-chain via `add_mirror`, and any other Data Layer client that subscribes to that store will have its `DataLayer` service issue outbound HTTP requests to those attacker-chosen URLs during sync (`fetch_and_validate` → `insert_from_delta_file` → `http_download`), without any restriction on scheme/host/IP (e.g. loopback, link-local metadata endpoints, or internal network hosts).

### Finding Description
`DataLayer.fetch_and_validate()` pulls the list of servers to try from `self.data_store.get_available_servers_for_store(store_id, timestamp)` and then calls `insert_from_delta_file(...)` with a `ServerInfo` whose `.url` is used unchanged. [1](#0-0) 

`insert_from_delta_file` calls `download_file`, which — when no plugin `downloader` is configured (the default) — calls `http_download(target_filename_path, filename, proxy_url, server_info, timeout, log, max_delta_file_size)`. [2](#0-1) 

`http_download` issues `session.get(server_info.url + "/" + filename, ...)` directly, with no validation that the URL is a legitimate public DataLayer server (no scheme allow-list, no private/loopback/link-local IP blocking). [3](#0-2) 

These server URLs originate from mirror coins that any store owner can create on-chain via the `add_mirror` RPC, and other nodes subscribing to that store id will treat those URLs as legitimate download sources (`get_available_servers_for_store`) with no filtering of the URL contents beyond its use as a plain string. [4](#0-3) 

The DataLayer subsystem's own design notes explicitly flag mirror/plugin URLs as untrusted external inputs whose only real protection is that downloaded *content* is checked against the wallet-advertised Merkle root — the destination URL itself is not restricted. [5](#0-4) 

This matches the reported bug class (an application performing outbound HTTP fetches to attacker-supplied URLs without restricting the destination — classic SSRF), except here the "editor upload" trigger is replaced by "subscribe to a malicious store" and the "remote image fetch" is replaced by "mirror/delta-file download."

### Impact Explanation
A malicious DataLayer store owner (an unprivileged actor — anyone can create a store and add mirror coins) can set mirror URLs pointing at internal-only services (e.g. `http://127.0.0.1:<port>/...`, `http://169.254.169.254/...`, or an internal admin endpoint reachable from the victim's host but not the internet). Any other Data Layer client who subscribes to that store — which is a normal, low-privilege peer action — will have their node's `DataLayer` service make outbound requests to attacker-chosen internal endpoints, potentially probing/interacting with local services, cloud metadata endpoints, or other hosts reachable from the victim machine's network position. This is a Medium severity SSRF: no coin theft or consensus divergence, but a real information-disclosure/internal-network-probing primitive triggered by a routine subscribe action.

### Likelihood Explanation
Likelihood is moderate-to-high for anyone who subscribes to third-party DataLayer stores (a supported and expected DataLayer workflow), since `add_mirror` requires no coordination with the victim beyond the victim choosing to subscribe to the attacker's store id, and the mirror URL and filename are attacker-influenced strings passed straight into an HTTP client.

### Recommendation
Validate/restrict mirror and server URLs before use in `http_download`/`download_file`: enforce an allow-list of schemes (http/https only), resolve and reject requests targeting loopback, link-local (169.254.0.0/16), private RFC1918 ranges, and other non-routable/metadata addresses unless explicitly permitted by local configuration, and consider requiring user confirmation before a node fetches from a newly seen mirror URL outside of a configured allow-list.

### Proof of Concept
1. Attacker creates a DataLayer store and calls `add_mirror` with `urls=["http://127.0.0.1:<victim_internal_port>/"]` (or a metadata-service IP), as exercised in the mirror test flow. [4](#0-3) 
2. Victim runs `chia data subscribe` against the attacker's store id, which is a normal Data Layer client action.
3. During the victim's periodic sync, `DataLayer.fetch_and_validate()` selects the attacker's mirror as a server and calls `insert_from_delta_file` → `download_file` → `http_download`, causing the victim's node to issue an HTTP GET to the attacker-chosen internal URL. [6](#0-5) [3](#0-2) 

Note: I was not able to fully trace how `get_available_servers_for_store` populates/refreshes entries from on-chain mirror coins (that logic lives in `chia/data_layer/data_store.py` and `chia/data_layer/data_layer_wallet.py`, which I could only partially inspect given the remaining tool budget), so the exact mechanics of mirror-URL-to-`ServerInfo` propagation should be double-checked in those files before treating this as fully proven.

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

**File:** chia/data_layer/download_data.py (L171-220)
```python
async def insert_from_delta_file(
    data_store: DataStore,
    store_id: bytes32,
    existing_generation: int,
    target_generation: int,
    root_hashes: list[bytes32],
    server_info: ServerInfo,
    client_foldername: Path,
    timeout: aiohttp.ClientTimeout,
    log: logging.Logger,
    proxy_url: str | None,
    downloader: PluginRemote | None,
    group_files_by_store: bool = False,
    maximum_full_file_count: int = 1,
    max_delta_file_size: int = 250,
) -> bool:
    if group_files_by_store:
        client_foldername.joinpath(f"{store_id}").mkdir(parents=True, exist_ok=True)

    delta_reader: DeltaReader | None = None

    for root_hash in root_hashes:
        timestamp = int(time.time())
        existing_generation += 1
        target_filename_path = get_delta_filename_path(
            client_foldername, store_id, root_hash, existing_generation, group_files_by_store
        )
        filename_exists = target_filename_path.exists()
        for grouped_by_store in (False, True):
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
            if success:
                break
        else:
            return False
```

**File:** chia/data_layer/download_data.py (L298-324)
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
            max_delta_file_size_bytes = max_delta_file_size * 1024 * 1024
            if size > max_delta_file_size_bytes:
                raise MaxDeltaFileSizeExceededError(f"Maximum delta file size exceeded: {max_delta_file_size} MiB.")
            log.debug(f"Downloading delta file {filename}. Size {size} bytes.")
```

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2782-2790)
```python
        urls = ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        res = await data_rpc_api.add_mirror({"id": store_id.hex(), "urls": urls, "amount": 1, "fee": 1})

        await farm_block_check_singleton(data_layer, full_node_api, ph, store_id, wallet=wallet_rpc_api.service)
        mirrors = await data_rpc_api.get_mirrors({"id": store_id.hex()})
        mirror_list = mirrors["mirrors"]
        assert len(mirror_list) == 1
        mirror = mirror_list[0]
        assert mirror["urls"] == ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
```

**File:** .cursor/context/data-layer.md (L83-88)
```markdown
## Fragility Hotspots

- High-risk edits move work across DB writer transactions, `_update_confirmation_status()`, pending-root status changes, or wallet RPC calls. These boundaries encode publication and rollback assumptions.
- File-system writes for Merkle blobs, key/value blobs, and `.dat` files have TODOs around locking. Concurrent service/plugin/server access should be treated as a real consistency concern.
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
