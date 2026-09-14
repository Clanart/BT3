Confirmed: there is no SSRF guard anywhere in the DataLayer mirror/downloader/plugin URL path — no private/loopback/link-local/metadata-address filtering exists in `chia/data_layer/*` (the grep for `private|loopback|link.local|metadata|169.254` matches only unrelated modules like keychain, wallet, SSL, etc.). [1](#0-0) [2](#0-1) 

### Title
SSRF via unvalidated DataLayer mirror URLs fetched by peer nodes during subscription sync - (File: chia/data_layer/download_data.py)

### Summary
The DataLayer `add_mirror` RPC accepts an arbitrary, attacker-controlled list of URLs and publishes them on-chain via `dl_new_mirror`, with no validation of scheme, host, or IP range. Any other node subscribed to that store will automatically fetch from those URLs during its periodic sync loop, using a plain `aiohttp` GET/POST with no SSRF guard (no check against loopback, private, link-local, or cloud-metadata addresses such as `169.254.169.254`).

### Finding Description
`DataLayerRpcApi.add_mirror()` forwards the caller-supplied `urls` list directly to `DataLayer.add_mirror()`, which only checks that the list is non-empty before submitting it on-chain: [1](#0-0) [2](#0-1) . There is no scheme allow-list, no DNS/IP resolution check, and no rejection of internal, loopback, link-local, or cloud metadata addresses.

These mirror URLs are read back by every node that subscribes to the store through `update_subscriptions_from_wallet()`, which just strips trailing slashes and stores them as candidate server URLs: [3](#0-2) . During the periodic `fetch_and_validate()`/`update_subscription()` loop, the service picks a random server from that unvalidated list and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which issues a bare `aiohttp.ClientSession().get(server_info.url + "/" + filename, ...)` with no host/IP restriction: [4](#0-3) . The same lack of validation applies to the downloader/uploader plugin discovery calls (`get_downloader()`, `get_uploaders()`, `check_plugins()`), which POST to `PluginRemote` URLs, though those URLs come from local config rather than chain data: [5](#0-4) .

Because mirror URLs are propagated on-chain (any subscriber of the store receives them via `dl_get_mirrors`), a single authenticated Data Layer operator can register a malicious mirror URL (e.g. `http://169.254.169.254/latest/meta-data/iam/security-credentials/` or an internal service address) that every other node subscribed to the same store will automatically and periodically request, without any user interaction on the victim side.

### Impact Explanation
This allows a Data Layer participant to force other subscribing nodes' hosts to make outbound HTTP requests to arbitrary internal or cloud-metadata endpoints on a recurring basis (the sync loop runs periodically), potentially exfiltrating cloud IAM credentials, reaching internal-only management APIs, or probing internal network topology from the victim's infrastructure. This matches the CVSS profile of the referenced WACRM SSRF (network-reachable, low privilege, confidentiality/integrity impact on internal services), scoped here to the DataLayer subscription sync path.

### Likelihood Explanation
Any user who can call the `add_mirror` DataLayer RPC (a standard, documented DataLayer operation, not privileged beyond normal DataLayer usage) can trigger this. Exploitation requires no interaction from the victim beyond having subscribed to the same store, which is the normal Data Layer replication use case.

### Recommendation
Add an SSRF guard analogous to the missing `isDeliverableUrl` check before both submitting mirror URLs on-chain and before the periodic fetch: validate scheme (`http`/`https` only), resolve the hostname, and reject loopback, private (RFC1918), link-local (including `169.254.169.254`), and other non-routable/reserved address ranges in `DataLayer.add_mirror()` and in `http_download()`/`download_file()` in `chia/data_layer/download_data.py` before making outbound requests.

### Proof of Concept
1. Node A creates a DataLayer store and calls `add_mirror` with `urls=["http://169.254.169.254/latest/meta-data/"]` (or an internal RFC1918 address) via `DataLayerRpcApi.add_mirror`.
2. Node B subscribes to the same store id and runs its normal periodic sync (`periodically_manage_data()` → `update_subscription()` → `update_subscriptions_from_wallet()` picks up the malicious URL from `dl_get_mirrors`).
3. Node B's `fetch_and_validate()` selects the malicious server and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, issuing an outbound GET to the attacker-chosen address with no destination filtering, as shown in `chia/data_layer/download_data.py:298-319`.
4. The request succeeds or fails depending on network reachability from Node B, but is sent regardless of whether the target is internal/metadata-only, demonstrating the missing SSRF guard.

### Citations

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
