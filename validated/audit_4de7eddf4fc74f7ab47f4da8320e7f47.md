## Title
SSRF via unauthenticated DataLayer mirror coins causes automatic outbound HTTP fetch to attacker-controlled URLs - (File: `chia/data_layer/data_layer_wallet.py`, `chia/data_layer/data_layer.py`, `chia/data_layer/download_data.py`)

### Summary
Any wallet holder can create a DataLayer "mirror" coin for **any** `launcher_id` (store id), including stores they do not own, and set its memo to an attacker-controlled URL list. `DataLayerWallet.create_new_mirror()` performs no check that the caller owns or controls the target singleton before broadcasting the mirror coin on-chain. Once confirmed, any node subscribed to (or owning) that store id will read the mirror URLs via `DataLayer.update_subscriptions_from_wallet()` and automatically issue outbound HTTP GET requests to them from `http_download()` during periodic background sync (`fetch_and_validate()`), with no restriction on scheme, host, or IP range. This is a classic SSRF primitive, matching CVE-2024-46468's bug class: an unprivileged network participant forces a victim node to make outbound requests to arbitrary internal/attacker-chosen endpoints.

### Finding Description
`DataLayerWallet.create_new_mirror()` builds a standard-wallet transaction whose only content is a mirror coin with a memo of `[launcher_id, *urls]`: [1](#0-0) 

There is no verification that `launcher_id` corresponds to a singleton owned/tracked by the calling wallet, nor any validation of the `urls` values (scheme, host, private-IP filtering). This is exposed directly via wallet RPC `dl_new_mirror`: [2](#0-1) 

and via the CLI `add_mirror` command, again with no ownership check on `store_id`: [3](#0-2) 

Once the mirror coin confirms on-chain, `dl_wallet_store.get_mirrors()`/`get_mirrors_for_launcher()` returns it to *any* node that queries mirrors for that `launcher_id` — this is not restricted to the store owner; it is a lookup keyed only by `launcher_id`: [4](#0-3) 

The `DataLayer` service periodically pulls these mirror URLs into its local subscription table: [5](#0-4) 

and `DataStore.update_subscriptions_from_wallet()` inserts them as active servers without any validation: [6](#0-5) 

Finally, `fetch_and_validate()` iterates over these subscribed server URLs and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which performs an unrestricted `aiohttp` GET request to `server_info.url + "/" + filename`: [7](#0-6) [8](#0-7) 

No allowlist, DNS-rebinding protection, or private/link-local IP filtering (e.g. `169.254.169.254` cloud metadata, `127.0.0.1`, internal RFC1918 ranges) is applied anywhere in this chain.

### Impact Explanation
An attacker who is just an ordinary wallet holder (no special privilege, not the store owner) can force any Data Layer node that subscribes to, owns, or otherwise processes a given `store_id` to make outbound HTTP requests to attacker-chosen destinations, on a recurring background schedule (`periodically_manage_data()`). This can be used to:
- Probe/access internal network services and cloud instance metadata endpoints reachable from the victim host, leaking sensitive information (matches the CVE's "information disclosure" impact).
- Trigger repeated connection attempts to arbitrary hosts/ports as part of normal sync, usable for internal network reconnaissance or as an SSRF pivot.

This fits the allowed impact categories via information disclosure/SSRF against a party that did not consent to the destination, reachable purely by an unprivileged "Data Layer client" broadcasting a standard transaction — no malicious peer, node, or leaked key required.

### Likelihood Explanation
Likelihood is high: creating a mirror coin only requires a funded wallet and a `dl_new_mirror`/`add_mirror` call with an arbitrary `launcher_id` and `urls`; no relationship to the target store's ownership is required or enforced. Any node that has ever subscribed to or created that store id will pick up the mirror URL automatically the next sync cycle.

### Recommendation
- In `DataLayerWallet.create_new_mirror()` (or at the RPC layer), require that the caller actually own/track the target `launcher_id` singleton before permitting mirror creation, or clearly document/restrict that mirrors are untrusted, attacker-supplied network hints.
- In `DataStore.update_subscriptions_from_wallet()` / `http_download()`, validate mirror URLs before use: enforce `http`/`https` scheme, reject loopback/link-local/private IP ranges and cloud metadata addresses, and consider requiring explicit user opt-in/allowlisting of mirror hosts before the service will automatically fetch from them.
- Add per-host rate limiting/circuit breaking distinct from the existing per-server backoff, and log/flag first-use of a new mirror host for operator visibility.

### Proof of Concept
1. Attacker wallet (no relation to target store) calls:
   ```
   dl_new_mirror(launcher_id=<victim_store_id>, amount=1, urls=["http://169.254.169.254/latest/meta-data/"], fee=...)
   ```
   via wallet RPC or `chia data add_mirror -id <victim_store_id> -u http://169.254.169.254/latest/meta-data/`.
2. Transaction confirms on-chain; the mirror coin's memo is now publicly associated with `victim_store_id`.
3. Any node that is subscribed to (or owns) `victim_store_id` runs its periodic `update_subscription()` → `update_subscriptions_from_wallet()` (`chia/data_layer/data_layer.py:979-985`) and adds the attacker's URL as an active server.
4. On the next `fetch_and_validate()` cycle (`chia/data_layer/data_layer.py:642-694`), the victim node issues an outbound `GET` request via `http_download()` (`chia/data_layer/download_data.py:298-324`) to `http://169.254.169.254/latest/meta-data/...`, an SSRF against the victim's own infrastructure that the node operator never configured or approved.

### Citations

**File:** chia/data_layer/data_layer_wallet.py (L687-703)
```python
    async def create_new_mirror(
        self,
        launcher_id: bytes32,
        amount: uint64,
        urls: list[bytes],
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        await self.standard_wallet.generate_signed_transaction(
            amounts=[amount],
            puzzle_hashes=[create_mirror_puzzle().get_tree_hash()],
            action_scope=action_scope,
            fee=fee,
            memos=[[launcher_id, *(url for url in urls)]],
            extra_conditions=extra_conditions,
        )
```

**File:** chia/wallet/wallet_rpc_api.py (L3255-3274)
```python
    async def dl_new_mirror(
        self,
        request: DLNewMirror,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> DLNewMirrorResponse:
        """Add a new on chain message for a specific singleton"""
        if self.service.wallet_state_manager is None:
            raise ValueError("The wallet service is not currently initialized")

        dl_wallet = await self.service.wallet_state_manager.get_dl_wallet()
        async with self.service.wallet_state_manager.lock:
            await dl_wallet.create_new_mirror(
                request.launcher_id,
                request.amount,
                Mirror.encode_urls(request.urls),
                action_scope,
                fee=request.fee,
                extra_conditions=extra_conditions,
            )
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

**File:** chia/data_layer/dl_wallet_store.py (L334-352)
```python
    async def get_mirrors(self, launcher_id: bytes32) -> list[Mirror]:
        async with self.db_wrapper.reader_no_transaction() as conn:
            cursor = await conn.execute(
                "SELECT * from mirrors WHERE launcher_id=?",
                (launcher_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            mirrors: list[Mirror] = []

            for row in rows:
                confirmation_height = await execute_fetchone(
                    conn, "SELECT * FROM mirror_confirmations WHERE coin_id=?", (row[0],)
                )
                mirrors.append(
                    _row_to_mirror(row, None if confirmation_height is None else uint32(confirmation_height[1]))
                )

        return mirrors
```

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

**File:** chia/data_layer/data_store.py (L1668-1703)
```python
    async def update_subscriptions_from_wallet(self, store_id: bytes32, new_urls: list[str]) -> None:
        async with self.db_wrapper.writer() as writer:
            cursor = await writer.execute(
                "SELECT * FROM subscriptions WHERE from_wallet == 1 AND tree_id == :tree_id",
                {
                    "tree_id": store_id,
                },
            )
            old_urls = [row["url"] async for row in cursor]
            cursor = await writer.execute(
                "SELECT * FROM subscriptions WHERE from_wallet == 0 AND tree_id == :tree_id",
                {
                    "tree_id": store_id,
                },
            )
            from_subscriptions_urls = {row["url"] async for row in cursor}
            additions = {url for url in new_urls if url not in old_urls}
            removals = [url for url in old_urls if url not in new_urls]
            for url in removals:
                await writer.execute(
                    "DELETE FROM subscriptions WHERE url == :url AND tree_id == :tree_id",
                    {
                        "url": url,
                        "tree_id": store_id,
                    },
                )
            for url in additions:
                if url not in from_subscriptions_urls:
                    await writer.execute(
                        "INSERT INTO subscriptions(tree_id, url, ignore_till, num_consecutive_failures, from_wallet) "
                        "VALUES (:tree_id, :url, 0, 0, 1)",
                        {
                            "tree_id": store_id,
                            "url": url,
                        },
                    )
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
