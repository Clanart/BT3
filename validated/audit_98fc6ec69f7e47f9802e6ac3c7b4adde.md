### Title
Server-Side Request Forgery via unvalidated DataLayer mirror/subscription URLs enabling internal network access - (File: chia/data_layer/download_data.py)

### Summary
Chia's DataLayer service fetches attacker-supplied HTTP(S) URLs — from `subscribe()` RPC input and from on-chain mirror coin URLs synced from the wallet — with no blacklist, scheme restriction, or address validation, mirroring the incomplete-blacklist SSRF pattern described in the calibreweb advisory (CVE-2022-0766), where checks failed to block loopback/`0.0.0.0`-style addressing.

### Finding Description
`DataLayer.subscribe()` accepts a caller-supplied list of URLs and stores them verbatim as `ServerInfo` objects with no validation beyond stripping a trailing slash: [1](#0-0) 

Any store owner can also publish on-chain "mirror" URLs (via `add_mirror`, paid for with a fee) that are later read from wallet state and used by any node that subscribes to that store; the DataLayer service's `update_subscription`/`fetch_and_validate` flow randomizes and iterates these `servers_info` URLs and passes them to `insert_from_delta_file` → `download_file`: [2](#0-1) 

`download_file()` in turn calls `http_download()`, which performs an `aiohttp` GET directly against the attacker-controlled `server_info.url` with no scheme allow-list, no DNS/IP validation, and no blacklist of loopback, link-local, or `0.0.0.0`-style addresses: [3](#0-2) 

Likewise, `get_downloader()` and plugin upload/download helpers (`get_plugin_info`, `get_uploaders`) POST JSON to configured plugin URLs, and the S3 plugin's `handle_download`/`download` handlers parse and dereference attacker/store-controlled URLs (`urlparse(data["url"])`) with only filename/scheme checks, not host/IP validation: [4](#0-3) 

No IP/hostname denylist logic (e.g., checks for `127.0.0.1`, `0.0.0.0`, private ranges, or `localhost`) exists anywhere in the `chia/data_layer/` module — a grep for such patterns across the module returned no matches outside of test fixtures and the S3 plugin's unrelated `urlparse` scheme check. This is the same root cause class as the calibreweb advisory: a URL-fetching feature trusts attacker/peer-supplied URLs without validating against loopback/wildcard addresses.

### Impact Explanation
A DataLayer client (a local RPC caller running `dl_subscribe`, or any node that subscribes to a store whose owner published malicious on-chain mirror URLs) will cause the local `chia_data_layer` process to issue outbound HTTP requests to attacker-chosen destinations, including `127.0.0.1`, `0.0.0.0`, cloud metadata endpoints, or other services bound to the operator's internal network/localhost. Because the request is a plain `GET`/`POST` with predictable paths (`/<filename>`, `/handle_download`, `/plugin_info`) and headers under partial attacker influence, this can be used to probe or interact with internal-only services (e.g., other RPC ports on the same host, internal admin panels) that a remote attacker could not otherwise reach — classic SSRF impact.

### Likelihood Explanation
Likelihood is moderate-to-high for operators who run DataLayer and subscribe to third-party stores or use plugin mirrors: the URL is either supplied directly through the exposed RPC (`subscribe`) or planted on-chain by any user willing to pay the mirror fee, requiring no privileged access. The periodic `periodically_manage_data()` loop in `data_layer.py` automatically syncs subscriptions and triggers fetches without additional user interaction once a subscription exists.

### Recommendation
Add strict URL validation before any outbound fetch in `chia/data_layer/download_data.py::http_download`, `chia/data_layer/data_layer.py::get_downloader/get_uploaders/get_plugin_info`, and the S3 plugin's `download`/`handle_download` handlers: resolve the hostname and reject loopback (`127.0.0.0/8`, `::1`), unspecified (`0.0.0.0`, `::`), link-local, and private ranges unless explicitly allow-listed by the operator; enforce an HTTP(S) scheme allow-list; and consider disabling automatic redirect-following into disallowed ranges.

### Proof of Concept
1. As an unprivileged wallet/DataLayer RPC user, call `dl_subscribe` with `urls=["http://127.0.0.1:<internal-port>/"]` (or have another user publish an on-chain mirror with such a URL and get this node to subscribe).
2. The `periodically_manage_data()` background loop calls `fetch_and_validate()` → `insert_from_delta_file()` → `download_file()` → `http_download()` in [3](#0-2) , issuing an unauthenticated GET request from the DataLayer process to the attacker-chosen internal address, confirming SSRF with no destination validation.

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

**File:** chia/data_layer/s3_plugin_service.py (L241-298)
```python
    async def handle_download(self, request: web.Request) -> web.Response:
        self.update_instance_from_config()
        try:
            data = await request.json()
        except Exception as e:
            log.error(f"failed parsing request {request} {type(e).__name__} {e}")
            return web.json_response({"handle_download": False})

        store_id = bytes32.from_hexstr(data["store_id"])
        parse_result = urlparse(data["url"])
        for store in self.stores:
            if store.id == store_id and parse_result.scheme == "s3" and data["url"] in store.urls:
                return web.json_response({"handle_download": True, "urls": list(store.urls)})

        return web.json_response({"handle_download": False})

    async def download(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            url = data["url"]
            filename = data["filename"]
            group_files_by_store = data.get("group_files_by_store", False)
            max_delta_file_size = data.get("max_delta_file_size")
            if not isinstance(max_delta_file_size, int) or max_delta_file_size <= 0:
                max_delta_file_size = 250

            # filename must follow the DataLayer naming convention
            if not is_filename_valid(filename, group_files_by_store):
                return web.json_response({"downloaded": False})

            # Pull the store_id from the filename to make sure we only download for configured stores
            filename_store_id = bytes32.fromhex(filename[:64])
            parse_result = urlparse(url)
            should_download = False
            for store in self.stores:
                if store.id == filename_store_id and parse_result.scheme == "s3" and url in store.urls:
                    should_download = True
                    break

            if not should_download:
                return web.json_response({"downloaded": False})

            bucket_str = parse_result.netloc
            my_bucket = self.boto_resource.Bucket(bucket_str)
            trimmed_filename = filename[65:] if group_files_by_store else filename
            target_filename = self.get_path_for_filename(filename_store_id, trimmed_filename, group_files_by_store)
            # Create folder for parent directory
            target_filename.parent.mkdir(parents=True, exist_ok=True)
            max_delta_file_size_bytes = max_delta_file_size * 1024 * 1024
            remote_file_size = self.boto_resource.ObjectSummary(bucket_str, filename).size
            if remote_file_size > max_delta_file_size_bytes:
                log.warning(
                    "Skipping %s, size %s bytes exceeds max delta size %s MiB",
                    filename,
                    remote_file_size,
                    max_delta_file_size,
                )
                return web.json_response({"downloaded": False})
```
