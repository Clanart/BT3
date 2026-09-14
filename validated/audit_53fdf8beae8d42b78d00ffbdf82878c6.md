### Title
SSRF via Unrestricted Mirror/Subscription URLs in Data Layer Fetch Path - ([File: chia/data_layer/data_layer.py])

### Summary
Chia's Data Layer allows any wallet holder to create a "mirror" coin carrying arbitrary URL strings on-chain via `create_mirror_puzzle()`, and allows any Data Layer client to `subscribe()` to arbitrary URLs. These URLs are later dialed directly by the Data Layer service's HTTP client (`http_download`/`download_file`) with no validation of destination address, mirroring the exact bug class described in the Kargo SSRF advisory: attacker-controlled URLs are dialed by a privileged in-cluster service, with no restriction on link-local/metadata addresses.

### Finding Description
A wallet user can create a mirror coin for any DataLayer store by spending a coin to the mirror puzzle and embedding arbitrary URL bytes in the memos: `create_mirror_puzzle()` / `get_mirror_info()` in [1](#0-0) . When this mirror coin is observed on-chain, `DataLayerWallet.coin_added()` extracts the URL list from the spend and stores it via `self.wallet_state_manager.dl_store.add_mirror(...)` with no URL validation beyond decoding: [2](#0-1) .

Separately, the RPC `subscribe()` call lets a Data Layer client register arbitrary URLs for a store, and `update_subscriptions_from_wallet()` merges wallet-derived mirror URLs into the subscription table without validation: [3](#0-2) [4](#0-3) .

These URLs are later chosen (`random.shuffle(servers_info)`) and dialed directly during `fetch_and_validate()`, which calls `insert_from_delta_file()` → `download_file()` → `http_download()`, performing an outbound HTTP GET to the attacker-supplied URL with no restriction against link-local, loopback, or private/internal addresses: [5](#0-4) [6](#0-5) . The project's own internal documentation acknowledges this is an unvalidated external trust boundary: "Plugin and mirror URLs are external trust inputs" [7](#0-6) .

Additionally, `get_downloader()` POSTs a `handle_download` request directly to attacker/operator-configured plugin URLs and to the `d.url + "/handle_download"` endpoint with no address restriction: [8](#0-7) .

### Impact Explanation
Any wallet user (a low-privilege actor who only needs enough mojos to spend a coin creating a mirror, or any Data Layer client permitted to call `subscribe`) can cause a victim's Data Layer node to issue outbound HTTP requests to attacker-chosen destinations, including cloud instance metadata endpoints (e.g., `169.254.169.254`) or other internal-only services reachable from the node's network position. Since `download_file`/`http_download` provides no restriction on destination and forwards response content into local delta-file processing (and ultimately into the node's own served data/full-file outputs), this is a genuine SSRF vector analogous to the Kargo `http`/`http-download` promotion-step issue — an unauthenticated internal endpoint's data could be pulled into the node's storage/output pipeline or used purely to probe/exfiltrate reachable internal network configuration.

### Likelihood Explanation
Creating a mirror coin only requires an on-chain spend controllable by any wallet holder with sufficient mojos and no special Kargo-style RBAC gate; likewise `subscribe()` is exposed as a normal Data Layer RPC callable by any local Data Layer client. Because mirror URLs travel through the chain (memos on a `CREATE_COIN` condition) and are picked up automatically by any tracking node's `coin_added()` handler, the attack requires no privileged access and no interaction from the victim operator beyond running a Data Layer node with mirror tracking enabled (a common/default configuration).

### Recommendation
Introduce a safe HTTP transport for `http_download`, `download_file`, and `get_downloader` that resolves and validates destination addresses before dialing, rejecting link-local (169.254.0.0/16, fe80::/10) and other disallowed ranges, mirroring the remediation adopted upstream for Kargo. Apply the same validation to URLs accepted in `subscribe()`, mirror-derived subscriptions (`update_subscriptions_from_wallet`), and plugin/downloader URLs before they are persisted or dialed.

### Proof of Concept
1. Attacker wallet spends a coin to `create_mirror_puzzle()` for a target `store_id`, with memos `[launcher_id, b"http://169.254.169.254/latest/meta-data/iam/security-credentials/"]`.
2. Any Data Layer node tracking that `launcher_id` observes the coin via `DataLayerWallet.coin_added()`, decodes the URL, and calls `dl_store.add_mirror(...)`, adding it to `get_available_servers_for_store`.
3. On the next sync cycle, `fetch_and_validate()` selects this server and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, causing the node to issue an HTTP GET to the cloud metadata endpoint from within its own network context, with no destination-address restriction applied.

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L90-110)
```python
def create_mirror_puzzle() -> Program:
    return P2_PARENT.curry(Program.to(1))


MIRROR_PUZZLE_HASH = create_mirror_puzzle().get_tree_hash()


def get_mirror_info(
    parent_puzzle: Program | SerializedProgram, parent_solution: Program | SerializedProgram
) -> tuple[bytes32, list[bytes]]:
    assert type(parent_puzzle) is type(parent_solution)
    _, conditions = run_with_cost(parent_puzzle, INFINITE_COST, parent_solution)
    for condition in conditions.as_iter():
        if (
            condition.first().as_python() == ConditionOpcode.CREATE_COIN
            and condition.at("rf").as_python() == create_mirror_puzzle().get_tree_hash()
        ):
            memos: list[bytes] = condition.at("rrrf").as_python()
            launcher_id = bytes32(memos[0])
            return launcher_id, [url for url in memos[1:]]
    raise ValueError("The provided puzzle and solution do not create a mirror coin")
```

**File:** chia/data_layer/data_layer_wallet.py (L775-799)
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

**File:** chia/data_layer/data_store.py (L1668-1704)
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

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
