### Title
Attacker-controlled on-chain mirror URLs cause DataLayer nodes to make server-side HTTP requests to arbitrary hosts (SSRF) - ([File: chia/data_layer/download_data.py], [File: chia/data_layer/data_layer.py])

### Summary
Any wallet user can spend to a `create_mirror_puzzle()` coin embedding an arbitrary `launcher_id` and an arbitrary list of URLs in the coin memo. `DataLayerWallet.coin_added()` tracks this mirror for any launcher id it already knows about (owned or subscribed store), and `DataLayer.update_subscriptions_from_wallet()` / `periodically_manage_data()` then feed those attacker-supplied URLs directly into outbound HTTP requests (`http_download`/`get_downloader`) with no host/scheme restriction, analogous to GitLab's CVE-2020-13286 SSRF via user-controlled git configuration URLs.

### Finding Description
`create_mirror_puzzle()` and `get_mirror_info()` allow anyone to construct a coin whose `CREATE_COIN` condition targets the mirror puzzle hash and whose memo encodes `(launcher_id, urls...)`: [1](#0-0) 

When such a coin is added, `DataLayerWallet.coin_added()` fetches the parent spend, extracts `launcher_id` and `urls` via `get_mirror_info`, and — as long as that `launcher_id` is already tracked by the local wallet (which happens automatically for any subscribed or owned DataLayer store) — persists the mirror record with no validation of the URL content: [2](#0-1) 

`DataLayer.update_subscriptions_from_wallet()` pulls these mirror URLs verbatim from wallet RPC and stores them as subscription server URLs: [3](#0-2) 

`fetch_and_validate()` then randomly selects one of these attacker-controlled URLs and either issues a raw HTTP GET (`http_download`) or POSTs JSON containing the URL to a locally configured downloader plugin: [4](#0-3) 

`get_downloader()` similarly POSTs the untrusted `url` value to plugin endpoints: [5](#0-4) 

The user-facing `add_mirror` CLI/RPC path (`chia data add_mirror -u <url>`) also allows a store owner to publish arbitrary URLs on-chain without any validation: [6](#0-5) [7](#0-6) 

No code path was found (within index coverage) that restricts mirror/subscription URLs to public schemes/hosts or blocks loopback/link-local/internal addresses before `aiohttp` makes the request in `download_file`/`http_download`: [8](#0-7) 

This mirrors the GitLab SSRF pattern: a value fully controlled by an unprivileged actor (here, an on-chain mirror coin memo, reachable by any wallet holder who can spend a coin) is later used, without sanitization, as the target of a server-initiated network request made by another user's/operator's node.

### Impact Explanation
Any chain participant who can spend a coin to the mirror puzzle for a `launcher_id` that another node subscribes to (subscribing to public/well-known DataLayer stores is common) can force that remote node's DataLayer service to issue outbound HTTP(S) requests to attacker-chosen hosts, including internal/private network addresses, cloud metadata endpoints, or other services reachable only from the victim's network. This is classic SSRF impact: internal network reconnaissance/port scanning, hitting internal-only HTTP admin APIs, or triggering side effects on services that trust requests originating from the victim node. It does not directly move funds, but it is a genuine unauthorized network-reachability vulnerability triggered by a value an unprivileged party fully controls, consistent with the referenced CVE's class.

### Likelihood Explanation
Likelihood is Medium: it requires the victim to already be aware of (subscribed to or owning) the targeted `launcher_id`, and it requires the mirror-syncing/subscription loop (`periodically_manage_data`) to run, which happens automatically for any operator running a DataLayer node with subscriptions. The attacker only needs to spend a coin to a known, unauthenticated puzzle with a crafted memo — no privileged access or code execution is required.

### Recommendation
Validate and restrict mirror/subscription URLs before use: enforce an allow-list of schemes (http/https only), reject/normalize URLs resolving to loopback, link-local, private (RFC1918), and cloud-metadata IP ranges, and consider requiring explicit operator opt-in/allow-listing for URLs learned from on-chain mirror coins rather than trusting them implicitly, at `chia/data_layer/data_layer.py`'s `update_subscriptions_from_wallet`/`fetch_and_validate` and `chia/data_layer/download_data.py`'s `http_download`/`download_file`.

### Proof of Concept
1. Attacker identifies a `launcher_id` for a DataLayer store that a victim node subscribes to or owns (subscription lists are discoverable via public DataLayer RPC or by observing chain activity).
2. Attacker creates and spends a coin with `CREATE_COIN` to `create_mirror_puzzle().get_tree_hash()`, with memo `[launcher_id, b"http://169.254.169.254/latest/meta-data/"]` (or an internal service URL), analogous to using `chia data add_mirror -i <launcher_id> -u <malicious-url>` with an arbitrary launcher id the attacker does not own. [9](#0-8) 
3. Victim's `DataLayerWallet.coin_added()` observes the coin, decodes the memo, and records the mirror since the `launcher_id` is already tracked locally. [10](#0-9) 
4. Victim's `periodically_manage_data()`/`update_subscription()` loop calls `update_subscriptions_from_wallet()`, pulling the malicious URL into the subscription's server list. [3](#0-2) 
5. On the next `fetch_and_validate()` cycle, the victim node issues an HTTP request to the attacker-chosen URL via `http_download`, confirming SSRF against the victim's network. [11](#0-10)

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L90-109)
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

**File:** chia/cmds/data.py (L474-511)
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

**File:** chia/data_layer/download_data.py (L110-168)
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

    log.info(f"Using downloader {downloader} for store {store_id.hex()}.")
    request_json = {
        "url": server_info.url,
        "client_folder": str(client_foldername),
        "filename": filename,
        "group_files_by_store": group_downloaded_files_by_store,
        "max_delta_file_size": max_delta_file_size,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                downloader.url + "/download",
                json=request_json,
                headers=downloader.headers,
                timeout=timeout,
            ) as response:
                res_json = await response.json()
                assert isinstance(res_json["downloaded"], bool)
                return res_json["downloaded"]
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        log.error(f"download_file could not get response from plugin {downloader}: {type(e).__name__}: {e}")
        return False
```
