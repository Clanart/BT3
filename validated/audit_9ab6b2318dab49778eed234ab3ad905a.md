### Title
Unauthenticated Mirror-Coin Injection Enables Server-Side Request Forgery in DataLayer Sync - (File: `chia/data_layer/data_layer_wallet.py`)

### Summary
Any user who can submit a spend bundle can create a "mirror" coin carrying an arbitrary URL list tied to any DataLayer store/launcher id. If a victim's wallet is already tracking that launcher id (a normal, unprivileged DataLayer client action), the wallet automatically records the attacker-chosen URLs as a mirror server for that store. The DataLayer service then periodically fetches from all known mirror URLs using `aiohttp`, without any restriction on scheme or destination, resulting in server-side requests to attacker-controlled hosts — the same bug class as CVE-2018-20497 (GitLab SSRF via unauthenticated/attacker-supplied URL fed into an internal outbound fetch feature).

### Finding Description
`DataLayerWallet.coin_added()` watches for coins with `puzzle_hash == create_mirror_puzzle().get_tree_hash()`. When such a coin appears, it decodes `launcher_id` and `urls` from the parent coin's spend via `get_mirror_info()` and, if `self.wallet_state_manager.dl_store.is_launcher_tracked(launcher_id)` is true, stores those URLs as a `Mirror` for that launcher id — with no check that the coin's creator is the store owner or otherwise authorized: [1](#0-0) 

The mirror puzzle itself is a generic `P2_PARENT` puzzle curried with `1`, meaning it can be spent to create a `CREATE_COIN` with any memo payload (`launcher_id`, arbitrary `urls`) by anyone controlling any coin — no relationship to the target singleton is required: [2](#0-1) 

`is_launcher_tracked` is true whenever the wallet is subscribed to or owns that DataLayer store — subscribing to a store id is a normal, unprivileged Data Layer client action (`dl_track_new` / DataLayer `subscribe`), so an attacker only needs to know a victim is tracking a given `launcher_id` (public on-chain identifier) to inject mirror URLs for it.

Once persisted, these mirror URLs feed `DataLayer.fetch_and_validate()`, which shuffles `get_available_servers_for_store()` results (including untrusted mirror URLs) and issues outbound HTTP requests to whichever URL is selected: [3](#0-2) 

The actual network request is performed by `download_file()`/`http_download()` in `download_data.py`, which builds a request directly from `server_info.url` without validating scheme or host (no blocklist for loopback/link-local/metadata addresses): [4](#0-3) 

The project's own module notes explicitly flag mirror URLs as an external trust boundary that must not be treated as trusted, underscoring that this is a known-risky area of the code: [5](#0-4) 

### Impact Explanation
This allows an unauthenticated remote party (any spend-bundle submitter) to force a victim's DataLayer node process to make outbound HTTP requests to attacker-chosen destinations — including internal-network or loopback addresses, cloud metadata endpoints, or other locally-reachable services — purely by broadcasting a cheap on-chain transaction referencing a launcher id the victim tracks. This is a genuine SSRF primitive reachable from a single submitted spend bundle, matching the CVE-2018-20497 bug class (attacker-controlled URL fed to a server-side fetch feature), and can be used for internal network reconnaissance, interaction with internal-only HTTP services, or triggering repeated outbound connections (amplification/probing) against arbitrary hosts.

### Likelihood Explanation
Likelihood is high for any node operating DataLayer with active subscriptions: creating a mirror coin only requires a standard coin spend with a `CREATE_COIN` to `MIRROR_PUZZLE_HASH` with the target `launcher_id` and desired URLs as memos, and no fee/authorization gate beyond normal transaction fees. Launcher ids of DataLayer stores are public identifiers (used for subscriptions and offers), making it straightforward for an attacker to target known/subscribed stores.

### Recommendation
- Restrict mirror-coin URL acceptance so only the store owner (or an explicitly signed authorization) can register mirror URLs used for automated fetch, or clearly separate "advisory/unverified" mirrors from ones actually used by `fetch_and_validate()`.
- Apply strict SSRF hardening to `http_download()`/`download_file()`: allow only `http`/`https` schemes, resolve and reject loopback/link-local/private/metadata IP ranges, and enforce redirect validation.
- Consider requiring operator opt-in/allowlisting before any mirror URL discovered on-chain is used for outbound fetches.

### Proof of Concept
1. Victim runs a DataLayer node and subscribes to (tracks) a public store with `launcher_id = L` via normal `dl_track_new`/subscribe RPC.
2. Attacker submits a standard spend bundle creating a coin with `puzzle_hash = create_mirror_puzzle().get_tree_hash()` and memo `[L, b"http://169.254.169.254/latest/meta-data/"]` (or any internal target URL), funded from attacker's own coins.
3. Once confirmed, the victim's `DataLayerWallet.coin_added()` sees the mirror coin, confirms `is_launcher_tracked(L)` is true, and stores the attacker URL as a `Mirror` for `L` via `dl_store.add_mirror()`.
4. On the victim's next `periodically_manage_data()` cycle, `update_subscription()`→`fetch_and_validate()` selects the attacker's mirror URL from `get_available_servers_for_store()` and issues an outbound HTTP GET to it via `http_download()`, completing the SSRF.

### Citations

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

**File:** chia/data_layer/download_data.py (L110-146)
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

**File:** .cursor/context/data-layer.md (L86-89)
```markdown
- File-system writes for Merkle blobs, key/value blobs, and `.dat` files have TODOs around locking. Concurrent service/plugin/server access should be treated as a real consistency concern.
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
- DataLayer wallet code depends on singleton CLVM structure, odd singleton amounts, lineage proofs, and offer solver field names. Changes in wallet puzzle drivers or offer summaries can break this module without direct edits here.
```
