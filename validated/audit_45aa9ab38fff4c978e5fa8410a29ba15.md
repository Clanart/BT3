### Title
SSRF via attacker-controlled DataLayer mirror URLs auto-adopted into wallet-tracked subscriptions - (File: chia/data_layer/data_layer_wallet.py)

### Summary
A Data Layer client's node automatically ingests mirror URLs from on-chain mirror coins for any launcher_id it tracks, then a background sync loop issues outbound HTTP requests to those URLs with no allowlisting or private/internal IP blocking, mirroring the Coolify `api_url` SSRF pattern (attacker-controlled URL used as base for unrestricted server-side HTTP fetch).

### Finding Description
Anyone can create a "mirror" coin that publishes an arbitrary list of URLs for an arbitrary `launcher_id`, with no ownership check tying the mirror to the actual store owner: `DataLayerWallet.create_new_mirror()` builds a standard coin whose puzzle is `create_mirror_puzzle()` and whose memos embed `[launcher_id, *urls]` [1](#0-0) .

When any node syncs and sees a coin created with the mirror puzzle hash, `DataLayerWallet.coin_added()` decodes the memo to get `launcher_id` and the URL list. If that `launcher_id` happens to be tracked by the local wallet (i.e., the operator has ever subscribed to, or owns, that store), the URLs are stored as a `Mirror` with no further validation of the URL contents or host: [2](#0-1) .

The DataLayer service periodically pulls these wallet-known mirror URLs into its local subscription table via `update_subscriptions_from_wallet()`, again with no validation: [3](#0-2)  and [4](#0-3) .

`DataLayer.fetch_and_validate()` then shuffles the servers for the store and, for each `server_info.url`, calls `insert_from_delta_file()` → `download_file()`, which — unless a downloader plugin is configured — calls `http_download()` directly against the attacker-supplied URL, with no allowlist or private-IP/metadata-IP blocking: [5](#0-4)  and [6](#0-5) .

The `get_plugin_info()` / `get_downloader()` paths similarly POST to plugin-configured or wallet-sourced URLs without validation [7](#0-6) [8](#0-7) .

### Impact Explanation
This lets an unprivileged, unauthenticated (from the victim's perspective) chain participant force a victim's DataLayer node to issue outbound HTTP requests to attacker-chosen destinations — including cloud metadata endpoints (e.g. `169.254.169.254`) or internal-only services on the victim's LAN/host — merely by publishing a mirror coin for a `launcher_id` the victim already tracks (which is discoverable/public since store subscriptions and DL offers are commonly shared). This is a genuine SSRF: the same bug class as the Coolify GithubApp `api_url` issue, where a user-influenced field is used as the base URL for server-side requests without allowlisting or private-IP blocking.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to know a `launcher_id` that some victim node tracks (common for any store the victim has subscribed to, offered/traded, or otherwise become interested in — these values are frequently shared publicly for DataLayer stores and offers) and to spend a small amount to create a mirror coin with a malicious memo. No signature from the store owner or any relationship to the singleton owner is required to publish a mirror for that `launcher_id`; the mirror puzzle/coin creation is unauthenticated with respect to the target store.

### Recommendation
- Validate and restrict mirror/download/plugin URLs before use: enforce an allowlist of schemes (https/http), reject loopback/link-local/private/multicast/metadata IP ranges (e.g. `169.254.169.254`, `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, IPv6 equivalents), and resolve DNS before connecting to prevent DNS-rebinding bypass.
- Require explicit user opt-in/confirmation before auto-adopting wallet-observed mirror URLs into `update_subscriptions_from_wallet()`, rather than silently trusting on-chain memo data.
- Consider requiring mirrors to be created only by the actual store owner (signed/verified relationship to the launcher singleton) rather than allowing arbitrary third parties to publish mirror URLs for any `launcher_id`.
- Apply the same URL validation to `PluginRemote` targets sourced from configuration if they can ever be influenced by non-operator input.

### Proof of Concept
1. Attacker identifies a `launcher_id` for a DataLayer store that the victim node tracks (subscribed, offered, or owns).
2. Attacker runs `chia data add_mirror --id <victim_tracked_launcher_id> --url http://169.254.169.254/latest/meta-data/ -a 1` (or targets an internal RPC/service port on the victim's network), which calls `add_mirror_cmd` → `DLNewMirror` RPC → `DataLayerWallet.create_new_mirror()` [1](#0-0) , broadcasting a coin with the malicious URL in its memo.
3. Once confirmed, the victim's wallet sync calls `DataLayerWallet.coin_added()`, which recognizes the mirror-puzzle coin and, because `launcher_id` is tracked, stores the attacker's URL via `dl_store.add_mirror()` [2](#0-1) .
4. On the victim's next `periodically_manage_data()` cycle, `update_subscriptions_from_wallet()` copies the malicious URL into the local subscription table [3](#0-2) .
5. `fetch_and_validate()` picks the malicious server and calls `download_file()` → `http_download()`, causing the victim node to issue an unrestricted HTTP request to the attacker's chosen internal/metadata endpoint [5](#0-4) [6](#0-5) .

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

**File:** chia/data_layer/data_layer_wallet.py (L775-800)
```python
    async def coin_added(
        self, coin: Coin, height: uint32, peer: WSChiaConnection, coin_data: object | None, sync_scope: WalletSyncScope
    ) -> None:
        if coin.puzzle_hash == create_mirror_puzzle().get_tree_hash():
            parent_state: CoinState = (
                await self.wallet_state_manager.wallet_node.get_coin_state([coin.parent_coin_info], peer=peer)
            )[0]
            parent_spend = await fetch_coin_spend(height, parent_state.coin, peer)
            assert parent_spend is not None
            launcher_id, urls = get_mirror_info(parent_spend.puzzle_reveal, parent_spend.solution)
            # Don't track mirrors with empty url list.
            if not urls:
                return
            if await self.wallet_state_manager.dl_store.is_launcher_tracked(launcher_id):
                ours: bool = await self.wallet_state_manager.get_wallet_for_coin(coin.parent_coin_info) is not None
                await self.wallet_state_manager.dl_store.add_mirror(
                    Mirror(
                        coin.name(),
                        launcher_id,
                        uint64(coin.amount),
                        Mirror.decode_urls(urls),
                        ours,
                        height,
                    )
                )
                await self.wallet_state_manager.add_interested_coin_ids([coin.name()])
```

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

**File:** chia/data_layer/download_data.py (L110-145)
```python
async def download_file(
    data_store: DataStore,
    target_filename_path: Path,
    store_id: bytes32,
    root_hash: bytes32,
    generation: int,
    server_info: ServerInfo,
    proxy_url: str | None,
    downloader: PluginRemote | None,
    timeout: aiohttp.ClientTimeout,
    client_foldername: Path,
    timestamp: int,
    log: logging.Logger,
    grouped_by_store: bool,
    group_downloaded_files_by_store: bool,
    max_delta_file_size: int,
) -> bool:
    if target_filename_path.exists():
        return True
    filename = get_delta_filename(store_id, root_hash, generation, grouped_by_store)

    if downloader is None:
        # use http downloader - this raises on any error
        try:
            await http_download(
                target_filename_path, filename, proxy_url, server_info, timeout, log, max_delta_file_size
            )
        except (asyncio.TimeoutError, aiohttp.ClientError, MaxDeltaFileSizeExceededError):
            new_server_info = await data_store.server_misses_file(store_id, server_info, timestamp)
            log.info(
                f"Failed to download {filename} from {new_server_info.url}."
                f"Miss {new_server_info.num_consecutive_failures}."
            )
            log.info(f"Next attempt from {new_server_info.url} in {new_server_info.ignore_till - timestamp}s.")
            return False
        return True
```
