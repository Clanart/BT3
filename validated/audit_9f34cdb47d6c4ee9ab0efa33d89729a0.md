Confirmed: `DataLayerServer` creates its `WebServer` without passing `ssl_context`, so `scheme` defaults to `"http"` and no client-certificate/TLS auth is applied to this listener — unlike every other Chia service (RPC, daemon, peer server) which mandates mutual TLS via `ssl_context_for_server()`. [1](#0-0) [2](#0-1) 

### Title
DataLayer HTTP file server exposes `.dat` store files with no authentication or authorization - (File: chia/data_layer/data_layer_server.py)

### Summary
`DataLayerServer.start()` binds a plaintext HTTP listener (`host_ip`/`host_port`, default `0.0.0.0:8575`) with two unauthenticated GET routes, `file_handler` and `folder_handler`, that read and return the contents of any file under `server_files_location` whose name passes only a filename-shape check. There is no TLS, no client certificate, no API key, and no peer/session check — any network client that can reach the port can enumerate and download DataLayer delta/full-tree files.

### Finding Description
`DataLayerServer.start()` constructs its `WebServer` without an `ssl_context`, so the underlying `WebServer.create()` defaults `scheme` to `"http"` and passes `ssl_context=None` into `web.TCPSite`, meaning the listener never enforces TLS or peer identity, in contrast to every other Chia network surface (`RpcServer`, daemon, peer `ChiaServer`) which is built through `ssl_context_for_server()` requiring mutual private-CA certificates. [1](#0-0) [3](#0-2) 

The two exposed routes, `file_handler` (`GET /{filename}`) and `folder_handler` (`GET /{tree_id}/{filename}`), perform only a filename-round-trip validation via `is_filename_valid()` and then open and return the raw file bytes with no session, token, ACL, or ownership check tying the request to a legitimate wallet/store owner: [4](#0-3) 

`is_filename_valid()` only checks that the name matches the deterministic `{store_id}-{node_hash}-{delta|full}-{generation}-v1.0.dat` format produced by `get_delta_filename`/`get_full_tree_filename`; it does not check whether the caller has any right to that store's data: [5](#0-4) 

Because DataLayer files are keyed by a public, deterministic 64-hex-char store id plus a monotonic generation counter (not a secret token), an unauthenticated remote party who learns or brute-forces a store id can retrieve the full historical delta/full-tree files for that store — including all key/value pairs ever committed to it — even for stores never intended to be shared, mirrored, or subscribed to by that party. This directly parallels the RosarioSIS advisory's bug class: a mechanism (static file server) that stores/serves sensitive application data with no access control, where the only practical protection is guessing-resistance of the filename, and here the "secret" component (store id) is not secret at all — it is the same value used throughout the public DataLayer/offer/wallet RPC surface.

### Impact Explanation
Any of the store's committed key/value history (arbitrary application data placed into DataLayer, which can include business, pricing, or other sensitive off-chain records) is exposed to anyone with network access to the `host_port`, without any credential, as long as they know or guess the store's launcher id — a non-secret identifier that is exchanged in offers, proofs, and RPC responses. This is a confidentiality violation (CWE-921/922 analog) at the DataLayer file-serving boundary; it does not require compromising the wallet, RPC, or the private CA infrastructure that protects every other Chia network surface.

### Likelihood Explanation
Likelihood is high for any deployment where `host_ip` is bound to a non-loopback address (the shipped default in `chia/util/initial-config.yaml` is `0.0.0.0`), since store ids are routinely shared as part of normal DataLayer usage (subscriptions, offers) and generation numbers are small monotonically increasing integers, making full history enumeration for a known store id straightforward.

### Recommendation
Require the DataLayer HTTP file server to authenticate/authorize requests before serving files — e.g., bind by default to loopback only, require mutual TLS with the private CA (consistent with `ssl_context_for_server()` used by RPC/daemon/peer servers), or introduce a per-store access token that must be presented alongside the filename. At minimum, document and default `host_ip` to a non-public interface, and treat store ids as non-secret identifiers rather than implicit access-control tokens.

### Proof of Concept
1. Start a `data_layer_http` service with default config (`host_ip: 0.0.0.0`, `host_port: 8575`) as shipped in `chia/util/initial-config.yaml`.
2. From an unauthenticated remote host, learn/obtain a target `store_id` (e.g., from a DataLayer offer, mirror URL, or public subscription list) and its known generations.
3. Issue plain HTTP requests: `GET http://<target-ip>:8575/{store_id}-{node_hash}-full-{generation}-v1.0.dat` (or the `/{tree_id}/{filename}` grouped form).
4. The server returns the full file content via `file_handler`/`folder_handler` with no authentication check, disclosing the store's committed key/value data. [4](#0-3) [6](#0-5)

### Citations

**File:** chia/data_layer/data_layer_server.py (L64-71)
```python
        self.webserver = await WebServer.create(
            hostname=self.host_ip,
            port=self.port,
            routes=[
                web.get("/{filename}", self.file_handler),
                web.get("/{tree_id}/{filename}", self.folder_handler),
            ],
        )
```

**File:** chia/data_layer/data_layer_server.py (L91-118)
```python
    async def file_handler(self, request: web.Request) -> web.Response:
        filename = request.match_info["filename"]
        if not is_filename_valid(filename):
            raise Exception("Invalid file format requested.")
        file_path = self.server_dir.joinpath(filename)
        with open(file_path, "rb") as reader:
            content = reader.read()
        response = web.Response(
            content_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment;filename={filename}"},
            body=content,
        )
        return response

    async def folder_handler(self, request: web.Request) -> web.Response:
        tree_id = request.match_info["tree_id"]
        filename = request.match_info["filename"]
        if not is_filename_valid(tree_id + "-" + filename):
            raise Exception("Invalid file format requested.")
        file_path = self.server_dir.joinpath(tree_id).joinpath(filename)
        with open(file_path, "rb") as reader:
            content = reader.read()
        response = web.Response(
            content_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment;filename={filename}"},
            body=content,
        )
        return response
```

**File:** chia/util/network.py (L43-72)
```python
    @classmethod
    async def create(
        cls,
        hostname: str,
        port: uint16,
        routes: Iterable[web.RouteDef] = (),
        max_request_body_size: int = 1024**2,  # Default `client_max_size` from web.Application
        ssl_context: ssl.SSLContext | None = None,
        keepalive_timeout: int = 75,  # Default from aiohttp.web
        shutdown_timeout: int = 60,  # Default `shutdown_timeout` from aiohttp.web_runner.BaseRunner
        prefer_ipv6: bool = False,
        logger: logging.Logger = web_logger,
        start: bool = True,
    ) -> WebServer:
        app = web.Application(client_max_size=max_request_body_size, logger=logger)
        runner = web.AppRunner(
            app,
            access_log=None,
            keepalive_timeout=keepalive_timeout,
            shutdown_timeout=shutdown_timeout,
        )

        self = cls(
            runner=runner,
            hostname=hostname,
            listen_port=uint16(port),
            scheme="https" if ssl_context is not None else "http",
            _ssl_context=ssl_context,
            _prefer_ipv6=prefer_ipv6,
        )
```

**File:** chia/util/network.py (L81-89)
```python
    async def start(self) -> None:
        await self.runner.setup()
        site = web.TCPSite(
            self.runner,
            self.hostname,
            int(self.listen_port),
            ssl_context=self._ssl_context,
        )
        await site.start()
```

**File:** chia/data_layer/download_data.py (L28-59)
```python
def is_filename_valid(filename: str, group_by_store: bool = False) -> bool:
    if group_by_store:
        if filename.count("/") != 1:
            return False
        filename = filename.replace("/", "-")

    split = filename.split("-")

    try:
        raw_store_id, raw_node_hash, file_type, raw_generation, raw_version, *rest = split
        store_id = bytes32(bytes.fromhex(raw_store_id))
        node_hash = bytes32(bytes.fromhex(raw_node_hash))
        generation = int(raw_generation)
    except ValueError:
        return False

    if len(rest) > 0:
        return False

    # TODO: versions should probably be centrally defined
    if raw_version != "v1.0.dat":
        return False

    if file_type not in {"delta", "full"}:
        return False

    generate_file_func = get_delta_filename if file_type == "delta" else get_full_tree_filename
    reformatted = generate_file_func(
        store_id=store_id, node_hash=node_hash, generation=generation, group_by_store=False
    )

    return reformatted == filename
```

**File:** chia/util/initial-config.yaml (L693-696)
```yaml
  # Data for running a data layer server.
  host_ip: 0.0.0.0
  host_port: 8575
  # Data for running a data layer client.
```
