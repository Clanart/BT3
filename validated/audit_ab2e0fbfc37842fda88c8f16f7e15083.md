### Title
Unvalidated user-supplied URLs in DataLayer `subscribe`/`add_mirror` RPCs enable server-side request forgery against internal network services - ([File: chia/data_layer/data_layer_rpc_api.py])

### Summary
The DataLayer RPC `subscribe` endpoint accepts an arbitrary list of `urls` from the local RPC caller with no scheme or host validation, stores them, and the DataLayer background sync loop later uses them to issue outbound HTTP requests (and, for `downloader` plugins, HTTP POST requests carrying the URL) on behalf of the node. This mirrors the CVE-2020-24139 pattern: an unvalidated `path`/URL parameter is used server-side to originate outbound requests, which an attacker can point at internal-only hosts/ports to conduct network reconnaissance or interact with local services.

### Finding Description
`DataLayerRpcApi.subscribe()` reads `urls` straight from the request dict with no validation of scheme, hostname, or target address: [1](#0-0) 

This flows into `DataLayer.subscribe()`, which only strips a trailing slash before persisting the URLs as `ServerInfo` records: [2](#0-1) 

Periodically, `DataLayer.fetch_and_validate()` iterates over these stored, attacker/user-supplied server URLs and either performs a plain HTTP download or asks a configured downloader plugin to fetch the URL, passing the raw URL through to the plugin over HTTP POST: [3](#0-2) [4](#0-3) 

The actual outbound network call happens in `download_file()`/`http_download()` in `download_data.py`, which builds requests directly from `server_info.url` without any allow-list, private-IP blocking, or scheme restriction (`chia/data_layer/download_data.py:110-169`). No component in this path validates that the URL does not point to `localhost`, link-local metadata addresses (e.g. `169.254.169.254`), or other internal-only services before the node's own process makes the request.

The project's own architecture notes acknowledge this exact class of risk but only in the context of not trusting downloaded *data*, not in terms of preventing SSRF via the destination itself: "Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots..." — no mention of validating the URL/destination before the outbound request is made: [5](#0-4) 

Tests corroborate that arbitrary loopback/local URLs are accepted and processed by the subscription and mirror flows without restriction, e.g. `http://127.0.0.1/8000`: [6](#0-5) [7](#0-6) 

The `add_mirror` path similarly accepts arbitrary URLs (only rejecting an empty list), which are advertised on-chain and can also be consumed by peers' subscription/fetch logic: [8](#0-7) 

### Impact Explanation
The DataLayer service (a full node component with local network reach) will issue outbound HTTP GET/POST requests to hosts and ports fully controlled by the RPC caller. This can be used to:
- Probe internal ports/services reachable only from the node's host or private network (port scanning, service fingerprinting), matching the referenced CVE's "identify open ports, local network hosts" impact.
- Send crafted requests to co-located local services (e.g., other RPC servers, metadata endpoints, or local plugin services) that trust connections originating from localhost, potentially triggering unintended actions on those services.
- When a downloader plugin is configured, the raw URL is also forwarded via `POST .../handle_download` and `POST .../download` to the plugin, expanding the SSRF surface to whatever the plugin does with unauthenticated attacker-controlled `url` values (e.g., the S3 plugin's `urlparse`-based bucket resolution).

This does not directly cause unauthorized coin movement or consensus divergence, but it does provide a genuine, unauthenticated-from-the-node's-perspective request-forgery primitive reachable by any local Data Layer RPC caller, satisfying the "no scheme/host validation before outbound request" bug class from the CVE.

### Likelihood Explanation
High reachability: any caller of the DataLayer RPC (`subscribe`, `add_mirror`) can supply arbitrary URLs with no validation, and the periodic sync loop (`fetch_and_validate`, `update_subscription`) automatically triggers the outbound request without further confirmation, on an interval controlled by `manage_data_interval`. No signature, ownership, or particular chain state is required to add a subscription URL — the test suite explicitly notes that subscribing to a store you do not own works the same way.

### Recommendation
Validate and constrain `urls` supplied to `subscribe`/`add_mirror`/`remove_subscriptions` before they are persisted or dereferenced: enforce an allow-listed scheme (e.g., `http`/`https` only), resolve and reject loopback/link-local/private-network/multicast addresses (unless explicitly permitted via config for the local test/dev usage), and apply the same restriction before forwarding URLs to downloader/uploader plugins. Consider making SSRF-sensitive DataLayer HTTP calls go through a single vetted helper that performs DNS resolution and IP-based filtering (re-resolved at connect time to avoid DNS-rebinding), similar to protections commonly added for CVE-2020-24139-class SSRF bugs.

### Proof of Concept
1. Start a DataLayer service and create/track a store id (own or one whose id is known/observed on-chain).
2. Call the DataLayer RPC `subscribe` with `urls=["http://169.254.169.254/latest/meta-data/"]` (or `http://127.0.0.1:<internal-port>/...`), as shown reachable via `DataLayerRpcApi.subscribe()`: [1](#0-0) 
3. Wait for the periodic `periodically_manage_data()`/`update_subscription()` cycle; `fetch_and_validate()` will select this URL and issue an HTTP request to it via `download_file()`/`http_download()`, from the DataLayer process's network context — confirmable by observing an inbound connection at the attacker-controlled internal listener.

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

**File:** chia/data_layer/data_layer.py (L647-705)
```python
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

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L846-849)
```python
        # This tests subscribe/unsubscribe to your own singletons, which isn't quite
        # the same thing as using a different wallet, but makes the tests much simpler
        response = await data_rpc_api.subscribe(request={"id": store_id.hex(), "urls": ["http://127.0.0.1/8000"]})
        assert response is not None
```

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2782-2800)
```python
        urls = ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        res = await data_rpc_api.add_mirror({"id": store_id.hex(), "urls": urls, "amount": 1, "fee": 1})

        await farm_block_check_singleton(data_layer, full_node_api, ph, store_id, wallet=wallet_rpc_api.service)
        mirrors = await data_rpc_api.get_mirrors({"id": store_id.hex()})
        mirror_list = mirrors["mirrors"]
        assert len(mirror_list) == 1
        mirror = mirror_list[0]
        assert mirror["urls"] == ["http://127.0.0.1/8000", "http://127.0.0.1/8001"]
        coin_id = mirror["coin_id"]

        res = await data_rpc_api.delete_mirror({"coin_id": coin_id, "fee": 1})
        await farm_block_check_singleton(data_layer, full_node_api, ph, store_id, wallet=wallet_rpc_api.service)
        mirrors = await data_rpc_api.get_mirrors({"id": store_id.hex()})
        mirror_list = mirrors["mirrors"]
        assert len(mirror_list) == 0

        with pytest.raises(RuntimeError, match="URL list can't be empty"):
            res = await data_rpc_api.add_mirror({"id": store_id.hex(), "urls": [], "amount": 1, "fee": 1})
```

**File:** chia/_tests/core/data_layer/test_data_store.py (L1068-1077)
```python
async def test_subscribe_unsubscribe(data_store: DataStore, store_id: bytes32) -> None:
    await data_store.subscribe(Subscription(store_id, [ServerInfo("http://127:0:0:1/8000", 1, 1)]))
    subscriptions = await data_store.get_subscriptions()
    urls = [server_info.url for subscription in subscriptions for server_info in subscription.servers_info]
    assert urls == ["http://127:0:0:1/8000"]

    await data_store.subscribe(Subscription(store_id, [ServerInfo("http://127:0:0:1/8001", 2, 2)]))
    subscriptions = await data_store.get_subscriptions()
    urls = [server_info.url for subscription in subscriptions for server_info in subscription.servers_info]
    assert urls == ["http://127:0:0:1/8000", "http://127:0:0:1/8001"]
```
