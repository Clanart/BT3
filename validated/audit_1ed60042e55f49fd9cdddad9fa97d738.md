### Title
Unvalidated Data Layer subscription/mirror URLs enable SSRF-style internal network scanning - (File: `chia/data_layer/data_layer.py`, `chia/data_layer/download_data.py`)

### Summary
The Koha CVE describes an SSRF where an authenticated user configures an internal server address, and the application performs outbound requests to it, letting the attacker infer internal-network service availability from response timing/behavior. Chia's DataLayer has a directly analogous pattern: a local RPC caller can register arbitrary, unvalidated URLs as subscription mirrors, and the DataLayer service will subsequently issue outbound HTTP requests to those URLs on its own background sync schedule, with observable success/failure/timing signals surfaced back through RPC-queryable server state.

### Finding Description
`DataLayer.subscribe()` accepts a caller-supplied list of URLs with no scheme, host, or address-range validation, storing them as `ServerInfo` entries for a store: [1](#0-0) 

Likewise, `add_mirror()` forwards arbitrary URLs to be published as a wallet-visible mirror with no validation beyond non-emptiness: [2](#0-1) 

These URLs are later dequeued and dialed out to by the background sync loop. `fetch_and_validate()` randomizes `servers_info` (built directly from the subscribed URLs) and, for each, calls `insert_from_delta_file()`/`download_file()`, catching connector errors distinctly from generic exceptions and logging per-server outcomes and backoff timing: [3](#0-2) 

The actual outbound network call is `http_download()` in `download_data.py`, which performs an unrestricted `aiohttp` GET to `server_info.url + "/" + filename` — no check that the host isn't a loopback/link-local/private/cloud-metadata address: [4](#0-3) 

Failures are recorded via `data_store.server_misses_file()`, which sets a differentiated `ignore_till`/miss-count backoff that is queryable and distinguishes connection failures (`aiohttp.client_exceptions.ClientConnectorError`, i.e., host unreachable/refused) from other exceptions (e.g., timeouts against a filtered/firewalled port, or a valid HTTP response with wrong content): [5](#0-4) 

Similarly, `get_downloader()` performs its own unrestricted `aiohttp.ClientSession().post()` to plugin remote URLs without host restriction: [6](#0-5) 

Because subscription/mirror URLs are entirely attacker-chosen, and the DataLayer node will periodically dial them from its own network position (inside whatever network segment the node/service is deployed in — cloud host, home LAN, VPC, etc.), a caller can enumerate internal hosts/ports by submitting many candidate URLs (e.g. `http://10.0.0.5:22/`, `http://169.254.169.254/latest/meta-data/`, `http://127.0.0.1:8555/`) and differentiate open/closed/filtered ports via: (a) distinct exception types (`ClientConnectorError` vs `TimeoutError` vs generic `ClientError`), (b) response timing (connect-refused is near-instant, connect-timeout takes the full `sock_connect`/`total` timeout window), and (c) RPC-visible subscription/mirror server state (miss counters, `ignore_till`) that reflects these outcomes.

### Impact Explanation
This lets a Data Layer client with only store-subscription/mirror privileges use the DataLayer node as a network scanning proxy against whatever internal network the node has access to, mapping reachable internal services (including potentially the node's own loopback RPC ports or cloud metadata endpoints) purely from response classification and timing — the same bug class and impact described in CVE-2026-26379 (SSRF via unvalidated server address, enabling internal network reconnaissance via response-time analysis). It does not directly cause fund loss, but it is a confidentiality/network-boundary violation reachable from an authenticated but otherwise low-privileged Data Layer RPC caller.

### Likelihood Explanation
Likelihood is moderate: it requires access to the DataLayer RPC (`subscribe`/`add_mirror`), which is gated the same way other local Chia RPCs are (bearer-token/cert authenticated local RPC), matching the "authenticated attacker" precondition in the Koha CVE. No further privilege (wallet keys, spend bundle validity, consensus access) is needed — the URLs are taken verbatim and dialed by the trusted service process.

### Recommendation
Validate subscription and mirror URLs before storing/dialing them: enforce scheme allow-lists, resolve and reject loopback/link-local/private/multicast/metadata address ranges (unless explicitly operator-permitted via config, e.g., for testing), and consider applying the existing `proxy_url` configuration path uniformly with output-address filtering. Additionally, normalize error handling in `fetch_and_validate()`/`http_download()` so that distinguishable timing/exception behavior between "refused", "filtered/timeout", and "valid but wrong content" is not observable through RPC-visible state to an untrusted-but-authenticated caller.

### Proof of Concept
1. As an authenticated Data Layer RPC caller, call `subscribe` with a store id and `urls=["http://<internal-target>:<port>/"]` for a range of internal IPs/ports (see `DataLayer.subscribe()` at `chia/data_layer/data_layer.py:895-902`).
2. Wait for `periodically_manage_data()`'s background loop to invoke `fetch_and_validate()` (`chia/data_layer/data_layer.py:606-705`), which dials each subscribed URL via `http_download()` (`chia/data_layer/download_data.py:298-319`).
3. Query subscription/server state (or observe wall-clock timing of the `subscribe`/sync cycle and subsequent miss counters/`ignore_till` set by `server_misses_file`) to distinguish: fast `ClientConnectorError` (port closed/refused) vs. full-timeout `TimeoutError` (port open/filtered) vs. success (HTTP service present).
4. Repeat across an internal IP/port range to build a map of reachable internal services, analogous to the Koha Z39.50/SRU SSRF scan technique.

### Citations

**File:** chia/data_layer/data_layer.py (L642-705)
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
                if success:
                    self.log.info(
                        f"Finished downloading and validating {store_id}. "
                        f"Wallet generation saved: {singleton_record.generation}. "
                        f"Root hash saved: {singleton_record.root}."
                    )
                    break
            except aiohttp.client_exceptions.ClientConnectorError:
                self.log.warning(f"Server {url} unavailable for {store_id}.")
            except Exception as e:
                self.log.warning(f"Exception while downloading files for {store_id}: {e} {traceback.format_exc()}.")
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

**File:** chia/data_layer/data_layer.py (L895-902)
```python
    async def subscribe(self, store_id: bytes32, urls: list[str]) -> Subscription:
        parsed_urls = [url.rstrip("/") for url in urls]
        subscription = Subscription(store_id, [ServerInfo(url, 0, 0) for url in parsed_urls])
        await self.wallet_rpc.dl_track_new(DLTrackNew(launcher_id=subscription.store_id))
        async with self.subscription_lock:
            await self.data_store.subscribe(subscription)
        self.log.info(f"Done adding subscription: {subscription.store_id}")
        return subscription
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
