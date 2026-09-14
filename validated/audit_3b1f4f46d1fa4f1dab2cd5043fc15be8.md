### Title
SSRF via unrestricted DataLayer mirror URL fetch enabling internal network port scanning - (File: chia/data_layer/download_data.py)

### Summary
DokuWiki's CVE-2016-7964 is an SSRF caused by `HTTPClient::sendRequest()` fetching arbitrary attacker-supplied media URLs with no restriction against private/internal address ranges. Chia's DataLayer module has an analogous pattern: any wallet owner can publish arbitrary mirror URLs on-chain for a DataLayer store via `add_mirror`, and any other DataLayer node/client that subscribes to that store will automatically fetch data from those URLs over plain HTTP with no validation that the target host/IP is not an internal/private address.

### Finding Description
`DataLayer.add_mirror()` lets any wallet publish an arbitrary list of URLs to the chain for a store via `wallet_rpc.dl_new_mirror(DLNewMirror(launcher_id=store_id, amount=amount, urls=urls, fee=fee, push=True), ...)` [1](#0-0) . The `urls` field is a free-form `list[str]` with no scheme/host/IP validation performed anywhere in the RPC request type [2](#0-1)  or in the `add_mirror` RPC handler [3](#0-2) .

Any other DataLayer node/client that subscribes to that store pulls these mirror URLs into its local subscription table via `update_subscriptions_from_wallet()`, which just strips trailing slashes and stores them verbatim [4](#0-3) . During the periodic sync loop, `fetch_and_validate()` iterates these attacker-controlled server URLs and issues outbound HTTP requests to them via `insert_from_delta_file()` → `download_file()` → `http_download()` [5](#0-4) .

`http_download()` performs a raw `aiohttp.ClientSession().get(server_info.url + "/" + filename, ...)` with no restriction on the resolved host being a private/loopback/link-local/cloud-metadata address (e.g., `127.0.0.1`, `10.0.0.0/8`, `192.168.0.0/16`, `169.254.169.254`) [6](#0-5) . There is no `ip_address`/`is_private` check anywhere in `download_data.py`, mirroring the exact root cause of CVE-2016-7964 (`HTTPClient::sendRequest()` fetching attacker-supplied URLs with no restriction on private-network destinations). The response status/size/content behavior differs based on filename validity checks (`is_filename_valid`) and HTTP status codes, giving the subscribing DataLayer operator an oracle for internal port/service scanning, since connection success/failure/timeout is observable and logged (`self.log.warning(f"Server {url} unavailable...")`) [7](#0-6) .

### Impact Explanation
An unprivileged Data Layer wallet user (the maker of a subscribed DataLayer offer/store) can publish arbitrary mirror URLs on-chain that will be automatically fetched by every other DataLayer node subscribing to that store. Because the fetch happens with no allowlist/denylist for private IP ranges, an attacker can use victim DataLayer nodes as an SSRF proxy to port-scan internal networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) or reach cloud metadata services, inferring open ports/services from connection timing, timeouts, and error classification differences surfaced in logs/exceptions. This is a reconnaissance/internal-network-exposure primitive rather than direct coin theft, but it is reachable purely from a normal Data Layer subscription flow with no privileged access required.

### Likelihood Explanation
High likelihood of reachability: `add_mirror` is a standard, documented DataLayer wallet operation exposed via CLI/RPC (`chia data add_mirror`) [8](#0-7) , requires no special privilege beyond owning a wallet, and any peer that subscribes to the attacker's store id will automatically and periodically fetch from the published URLs without operator interaction, per the periodic sync loop documented for `update_subscription()`/`fetch_and_validate()` [9](#0-8) .

### Recommendation
Validate and restrict mirror/downloader URLs before storing them and before issuing outbound HTTP requests in `download_data.py`'s `http_download()`/`download_file()`: resolve the hostname, reject loopback/link-local/private/multicast/reserved IP ranges (RFC1918, 127.0.0.0/8, 169.254.0.0/16, etc.) unless explicitly allowed via configuration, enforce an HTTPS-only or explicit allowlist policy for mirror URLs, and avoid following redirects to disallowed destinations. Add the same validation to `add_mirror`/`DLNewMirror` handling so operators are warned before publishing unsafe URLs on-chain.

### Proof of Concept
1. Attacker runs `chia data add_mirror --id <store_id> --url http://127.0.0.1:6379/ -a 1 -m 1` (or any internal target such as `http://169.254.169.254/latest/meta-data/` or `http://192.168.1.1:22/`), publishing the mirror coin on-chain via `add_mirror` → `dl_new_mirror` [1](#0-0) .
2. A victim operator's DataLayer node subscribes to `store_id` (e.g., via `chia data subscribe`), causing `update_subscriptions_from_wallet()` to ingest the attacker's URL list unmodified [4](#0-3) .
3. During the next sync cycle, `fetch_and_validate()` selects the attacker's URL from `servers_info` and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which issues an `aiohttp` GET to the internal target with no IP-range restriction [6](#0-5) .
4. The attacker observes differing response times/log messages (`ClientConnectorError` vs. timeout vs. HTTP status) across repeated mirror publications targeting different internal IPs/ports to map the victim's internal network, confirming the SSRF/port-scan primitive.

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

**File:** chia/data_layer/data_layer.py (L702-705)
```python
            except aiohttp.client_exceptions.ClientConnectorError:
                self.log.warning(f"Server {url} unavailable for {store_id}.")
            except Exception as e:
                self.log.warning(f"Exception while downloading files for {store_id}: {e} {traceback.format_exc()}.")
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

**File:** chia/wallet/wallet_request_types.py (L1853-1859)
```python
@streamable
@dataclass(frozen=True, kw_only=True)
class DLNewMirror(TransactionEndpointRequest):
    launcher_id: bytes32
    amount: uint64
    urls: list[str]

```

**File:** chia/data_layer/data_layer_rpc_api.py (L468-475)
```python
    async def add_mirror(self, request: dict[str, Any]) -> EndpointResult:
        store_id = request["id"]
        id_bytes = bytes32.from_hexstr(store_id)
        urls = request["urls"]
        amount = request["amount"]
        fee = get_fee(self.service.config, request)
        await self.service.add_mirror(id_bytes, urls, amount, fee)
        return {}
```

**File:** chia/data_layer/download_data.py (L298-323)
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
```

**File:** chia/cmds/data.py (L474-510)
```python
# NOTE: tx_endpoint
@data_cmd.command("add_mirror", help="Publish mirror urls on chain")
@create_data_store_id_option()
@click.option(
    "-a", "--amount", help="Amount to spend for this mirror, in mojos", type=int, default=0, show_default=True
)
@click.option(
    "-u",
    "--url",
    "urls",
    help="URL to publish on the new coin, multiple accepted and will be published to a single coin.",
    type=str,
    multiple=True,
)
@options.create_fee()
@create_rpc_port_option()
@options.create_fingerprint()
def add_mirror(
    id: bytes32,
    amount: int,
    urls: list[str],
    fee: uint64 | None,
    data_rpc_port: int,
    fingerprint: int | None,
) -> None:
    from chia.cmds.data_funcs import add_mirror_cmd

    run(
        add_mirror_cmd(
            rpc_port=data_rpc_port,
            store_id=id,
            urls=urls,
            amount=amount,
            fee=fee,
            fingerprint=fingerprint,
        )
    )
```

**File:** .cursor/context/data-layer.md (L61-65)
```markdown
- `update_subscription()` performs four ordered steps: sync subscription URLs from wallet mirrors, fetch/validate remote data, upload local files for owned/current data, then prune old full files. Reordering these affects mirror discovery and file availability.
- Wallet reachability failures are intentionally non-fatal. Subscription tracking stops early if the wallet RPC connection is unavailable and retries next cycle; per-subscription failures are logged without killing the loop.
- `fetch_and_validate()` uses wallet singleton history as the target generation/root sequence, randomizes eligible server URLs, tries plugin-specific or plain HTTP delta downloads, and marks mirror/server failures with backoff in the subscriptions table.
- Unsubscribe is queued and processed only after fetch jobs complete while holding `subscription_lock`, avoiding races between deletion of local subscription/data and active download/update work.
- Owned stores are treated as local publication sources even when they have no external mirror URLs. Subscribed stores are treated as replication targets whose chain roots are verified through wallet state.
```
