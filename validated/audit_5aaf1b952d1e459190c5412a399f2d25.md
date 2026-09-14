### Title
SSRF analog in DataLayer subscription/mirror fetch — server issues unrestricted outbound HTTP requests to attacker-supplied URLs - (File: chia/data_layer/download_data.py)

### Summary
Tiny File Manager's CVE-2025-46651 is a classic SSRF: a user-supplied URL is fetched by the server without restricting the target host, letting an attacker probe/reach internal-only network resources (localhost, internal services) via crafted hostnames. Chia's DataLayer subscription/mirror mechanism has the same bug-class shape: a store owner (any wallet user who can spend a `DL` singleton and pay the mirror coin amount) can publish arbitrary URLs on-chain via `add_mirror`, and any other DataLayer node/wallet that subscribes to that store will have its DataLayer service automatically issue outbound HTTP requests to those attacker-chosen URLs with no host/IP allow-listing.

### Finding Description
DataLayer stores maintain a set of "mirror"/subscription server URLs that are advertised on-chain by the store owner and consumed by other nodes to sync store contents. `DataLayer.fetch_and_validate()` pulls the list of `servers_info` for a store and, for each one, calls `insert_from_delta_file()` → `download_file()` with the raw `server_info.url` [1](#0-0) .

`download_file()` in turn calls `http_download()`, which performs an unrestricted `aiohttp` `GET` request directly against `server_info.url + "/" + filename` with no validation of scheme, hostname, or IP range (no rejection of `127.0.0.1`, link-local, or other internal addresses, and no DNS-rebinding protection): [2](#0-1) 

The mirror/subscription URL set is populated from wallet state via `add_mirror`/`get_mirrors` (DL wallet RPCs) and then synced into the DataLayer service's subscription table as part of `update_subscription()`, per the documented flow: "sync subscription URLs from wallet mirrors, fetch/validate remote data, upload local files..." [3](#0-2) . The design notes explicitly flag this as an externally-trusted input that is not host-validated: "Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict" [4](#0-3)  — note this only calls out validating the *downloaded data*, not the *destination host* of the outbound request itself.

Any user who owns (or controls) a DataLayer store can add mirror URLs pointing at internal-only endpoints (e.g., `http://127.0.0.1:<port>/...`, cloud metadata endpoints, or internal-network hosts) via the on-chain `add_mirror` mechanism. Once other nodes subscribe to that store (a normal, expected operation for anyone wanting to sync that store's data), their `DataLayer` service — running with the operator's network position/trust — will automatically perform outbound requests to attacker-chosen targets during periodic `update_subscription()`/`fetch_and_validate()` cycles, with no opt-out or destination filtering.

### Impact Explanation
This is a genuine SSRF-class analog: it lets an untrusted store owner cause other participants' DataLayer nodes to make server-initiated requests to arbitrary hosts, including internal-only services reachable from the node's network position (localhost services, internal RPC ports, cloud metadata services, etc.), enabling port scanning/service fingerprinting of internal networks and potential further exploitation depending on what is reachable. It does not directly cause coin-state divergence, unauthorized spend, or currency inflation, so the blast radius is confined to information disclosure/internal network reconnaissance triggered by an unprivileged (but data-layer-participating) counterparty — matching the CVSS 4.3/Medium severity of the original report.

### Likelihood Explanation
Likelihood is meaningful but bounded: the attacker must control a DataLayer store that a victim node has chosen to subscribe to (or is otherwise induced to subscribe to, e.g., via an offer that references that store, per the offer/proof coupling in DataLayer). Since subscribing to arbitrary/untrusted stores is a normal, expected DataLayer workflow (subscriber-facing feature, not admin-only), this is reachable by any DataLayer-participating wallet/CLI user without special privileges.

### Recommendation
Validate and restrict mirror/subscription/downloader/uploader URLs before they are used for outbound HTTP fetches in `http_download()` (`chia/data_layer/download_data.py`) and `get_downloader()`/`upload_files()` (`chia/data_layer/data_layer.py`): resolve the hostname and reject requests targeting loopback, link-local, private, and other non-routable/internal address ranges (and re-check on each connection to avoid DNS-rebinding), unless the operator has explicitly allow-listed such targets in config. Consider exposing this as a configurable SSRF-protection toggle similar to `proxy_url` handling, defaulting to safe behavior.

### Proof of Concept
1. Attacker creates a DataLayer store and, using the standard `dl_add_mirror` wallet RPC (see `data_layer_rpc_client.py add_mirror()`), publishes a mirror URL such as `http://127.0.0.1:8555/` (a local-only RPC/service port) or an internal service address on the victim's network.
2. Victim runs a DataLayer node and subscribes to the attacker's store (`subscribe()` RPC / `dl_track_new` flow), a normal action when wanting to replicate/query that store's data.
3. During the periodic `update_subscription()` cycle, `fetch_and_validate()` reads the attacker-controlled server URL from `get_available_servers_for_store()` and calls `download_file()` → `http_download()`, causing the victim's node process to issue an HTTP GET to `http://127.0.0.1:8555/<crafted-filename>` (or any other internal target), confirming the SSRF: the request originates from the victim's server against an internal-only endpoint chosen entirely by the attacker.

### Citations

**File:** chia/data_layer/data_layer.py (L642-666)
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

**File:** .cursor/context/data-layer.md (L61-63)
```markdown
- `update_subscription()` performs four ordered steps: sync subscription URLs from wallet mirrors, fetch/validate remote data, upload local files for owned/current data, then prune old full files. Reordering these affects mirror discovery and file availability.
- Wallet reachability failures are intentionally non-fatal. Subscription tracking stops early if the wallet RPC connection is unavailable and retries next cycle; per-subscription failures are logged without killing the loop.
- `fetch_and_validate()` uses wallet singleton history as the target generation/root sequence, randomizes eligible server URLs, tries plugin-specific or plain HTTP delta downloads, and marks mirror/server failures with backoff in the subscriptions table.
```

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
