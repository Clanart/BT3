### Title
DataLayer `subscribe` RPC allows unauthenticated-scheme SSRF against arbitrary/internal hosts, enabling network discovery - (File: `chia/data_layer/data_layer.py`)

### Summary
The DataLayer RPC `subscribe` endpoint accepts an arbitrary list of server URLs from the caller with no scheme, hostname, or network-range validation. Those URLs are persisted as subscription servers and are later used by the periodic sync loop to issue outbound HTTP GET requests. This mirrors CVE-2016-9185, where an authenticated user could get the service to make requests to attacker-chosen local/internal URLs and infer internal network configuration.

### Finding Description
`DataLayerRpcApi.subscribe()` takes the `urls` field straight from the JSON request and passes it to `DataLayer.subscribe()` without validating scheme or target host. [1](#0-0) 

`DataLayer.subscribe()` simply strips trailing slashes and stores each URL as a `ServerInfo` via `DataStore.subscribe()` — again, no scheme/host allow-listing (no rejection of `file://`, loopback, link-local, RFC1918, or cloud metadata addresses). [2](#0-1) 

The background sync loop (`periodically_manage_data()` → `update_subscription()` → `fetch_and_validate()`) later iterates over these attacker-supplied server URLs and issues real outbound requests: [3](#0-2) 

Concretely, `download_file()` calls `http_download()`, which performs `session.get(server_info.url + "/" + filename, ...)` against the stored URL with no destination restriction. [4](#0-3) 

The result (success/HTTP status/timeout/connection-refused/exception) is logged and reflected in subscription failure/backoff state (`server_misses_file`/`received_incorrect_file`), which is queryable through the `subscriptions` RPC and log output. This gives a local/authenticated DataLayer RPC caller an oracle to probe internal network hosts and ports (e.g., cloud metadata endpoints, other services on localhost/private ranges), exactly the "network discovery revealing internal network configuration" pattern in CVE-2016-9185.

### Impact Explanation
This is a server-side request forgery: a caller with DataLayer RPC access (a "Data Layer client" per the allowed reachable-surface categories) can direct the DataLayer service process to make network connections to arbitrary internal/local addresses and observe connectivity/error signals through logs and subscription state, enabling internal network reconnaissance from a node that otherwise should only fetch data from intended mirror servers. It does not by itself cause coin-state corruption, but it is a genuine confidentiality/network-boundary violation reachable from a normal local RPC/wallet-adjacent actor.

### Likelihood Explanation
High likelihood of reachability: `subscribe` is a documented, normal-use DataLayer RPC/CLI operation (`chia data subscribe --url ...`), requiring no special privilege beyond standard DataLayer RPC access, and the URL list is fully attacker-controlled with no validation anywhere in the call chain. [5](#0-4) 

### Recommendation
Validate and restrict subscription/mirror URLs before persisting or using them: enforce an allow-listed scheme (e.g., `https`), resolve and reject loopback/link-local/private/multicast/metadata IP ranges (or require an explicit opt-in config flag for local/internal testing), and consider limiting error/status detail exposed back through the `subscriptions` RPC/logs so failed connection attempts can't be used as a network probing oracle.

### Proof of Concept
1. As a local DataLayer RPC caller, create/own a store and call `subscribe` with a URL pointing at an internal target, e.g. `POST /subscribe {"id": "<store_id>", "urls": ["http://169.254.169.254"]}` or `["http://10.0.0.5:8080"]`. [1](#0-0) 
2. Wait for the periodic sync loop (`manage_data_interval`) to run `fetch_and_validate()`, which issues `session.get(url + "/" + filename)` against the attacker-chosen host. [4](#0-3) 
3. Observe via logs/`subscriptions` RPC status (success vs. `ClientConnectorError` vs. timeout) whether the internal host/port is reachable, effectively performing internal network discovery from the node. [6](#0-5)

### Citations

**File:** chia/data_layer/data_layer_rpc_api.py (L360-371)
```python
    async def subscribe(self, request: dict[str, Any]) -> EndpointResult:
        """
        subscribe to singleton
        """
        store_id = request.get("id")
        if store_id is None:
            raise Exception("missing store id in request")

        store_id_bytes = bytes32.from_hexstr(store_id)
        urls = request.get("urls", [])
        await self.service.subscribe(store_id=store_id_bytes, urls=urls)
        return {}
```

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

**File:** chia/cmds/data.py (L332-353)
```python
@data_cmd.command("subscribe", help="Subscribe to a store")
@create_data_store_id_option()
@click.option(
    "-u",
    "--url",
    "urls",
    help="Manually provide a list of servers urls for downloading the data",
    type=str,
    multiple=True,
)
@create_rpc_port_option()
@options.create_fingerprint()
def subscribe(
    id: bytes32,
    urls: list[str],
    data_rpc_port: int,
    fingerprint: int | None,
) -> None:
    from chia.cmds.data_funcs import subscribe_cmd

    run(subscribe_cmd(rpc_port=data_rpc_port, store_id=id, urls=urls, fingerprint=fingerprint))

```
