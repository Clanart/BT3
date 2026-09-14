### Title
SSRF via user-supplied DataLayer mirror/subscription URLs allows internal network probing by RPC-authorized users - (File: chia/data_layer/data_layer.py)

### Summary
An authorized Data Layer RPC caller can register arbitrary URLs (via `add_mirror` on-chain memo data, or the `subscribe` RPC) as replication/mirror servers for a DataLayer store. The DataLayer service subsequently makes outbound HTTP(S) requests to these attacker-chosen URLs with no destination validation (no blocking of loopback, RFC1918, or link-local/metadata addresses), analogous to the LXD SSRF where `can_create_images`-privileged users could make the daemon hit internal endpoints.

### Finding Description
`DataLayer.add_mirror()` accepts a caller-supplied list of URLs and writes them on-chain via `dl_new_mirror`, with no scheme/host allow-listing [1](#0-0) . Any store that subscribes to that launcher (`update_subscriptions_from_wallet`) pulls those URLs into the local subscription table verbatim, stripped only of a trailing slash — no validation of scheme, host, or IP range [2](#0-1) .

During periodic sync, `fetch_and_validate()` iterates `servers_info` (populated from those URLs) and calls `insert_from_delta_file`, which ultimately calls `download_file()` / `http_download()`, performing an `aiohttp` GET directly against the attacker-controlled URL with a proxy override honored from local config only [3](#0-2) . `http_download()` issues `session.get(server_info.url + "/" + filename, ...)` with no host/IP filtering, so the daemon will connect to loopback, private RFC1918 ranges, or cloud metadata addresses if the URL points there [4](#0-3) . Similarly, `get_downloader()` and `get_plugin_info()` POST JSON requests to configured plugin URLs and to the mirror-derived URL's `/handle_download` path without IP restriction [5](#0-4) [6](#0-5) .

Errors are surfaced distinctly (e.g., `ClientConnectorError` logged for unreachable servers vs. other exceptions for reachable-but-erroring hosts) [7](#0-6) , which an attacker with visibility into DataLayer logs (or timing behavior) could use for error-based port/service scanning of the machine's internal network, mirroring the LXD report's mechanism.

### Impact Explanation
This matches the "daemon/keychain/RPC authorization" and "Data Layer roots and proofs" reachable categories: an RPC-authorized Data Layer client (which need not be a privileged operator, just a wallet-adjacent DataLayer API consumer that can call `subscribe`/`add_mirror`) can force the node process to make arbitrary outbound connections to internal hosts, enabling internal service/port discovery from the node's network position. This is a Medium-severity SSRF analogous to CVE-2026-28385, not a privileged/operator-only issue, since mirror URLs originate from on-chain data that any peer's DataLayer store can publish and any subscriber will fetch.

### Likelihood Explanation
Likelihood is Medium: exploitation requires only that a victim node subscribes to (or otherwise processes) a store whose mirror URLs the attacker controls (a normal DataLayer replication workflow), and that the periodic subscription/mirror sync loop runs automatically per `update_subscription()`. No authentication bypass or crypto break is needed — only the ability to publish mirror URLs for a store that gets subscribed to.

### Recommendation
Validate and restrict destination hosts/IPs for all outbound DataLayer HTTP calls (`http_download`, `get_downloader`, `get_plugin_info`, mirror URL fetches): reject loopback, link-local, and RFC1918 addresses unless explicitly allow-listed by the node operator; resolve DNS and re-check the resolved IP before connecting (to prevent DNS-rebinding bypass); and add configuration to opt into probing internal ranges only for trusted/test deployments.

### Proof of Concept
1. Attacker creates/owns a DataLayer store and calls `add_mirror` (or otherwise controls a store's `Mirror.urls`) with a URL such as `http://127.0.0.1:8555/` or `http://169.254.169.254/latest/meta-data/` [1](#0-0) .
2. Victim node subscribes to that store id (`subscribe` RPC / `DataLayerRpcClient.subscribe`), causing `update_subscriptions_from_wallet` to store that URL as a server for the store [2](#0-1) .
3. The periodic sync loop calls `fetch_and_validate()` → `insert_from_delta_file()` → `download_file()` → `http_download()`, causing the node to issue an HTTP GET to the internal/loopback/metadata URL [4](#0-3) .
4. Differing exceptions (`ClientConnectorError` vs. other errors/timeouts) logged by `fetch_and_validate` allow the attacker (with log or side-channel access, or by observing subsequent retry/backoff timing recorded in `server_misses_file`) to infer whether the internal host/port is open.

### Citations

**File:** chia/data_layer/data_layer.py (L102-118)
```python
async def get_plugin_info(
    plugin_remote: PluginRemote, timeout: aiohttp.ClientTimeout
) -> tuple[PluginRemote, dict[str, Any]]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                plugin_remote.url + "/plugin_info",
                json={},
                headers=plugin_remote.headers,
                timeout=timeout,
            ) as response:
                ret = {"status": response.status}
                if response.status == 200:
                    ret["response"] = json.loads(await response.text())
                return plugin_remote, ret
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        return plugin_remote, {"error": f"{type(e).__name__}: {e}"}
```

**File:** chia/data_layer/data_layer.py (L661-694)
```python
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

**File:** chia/data_layer/download_data.py (L311-319)
```python
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
