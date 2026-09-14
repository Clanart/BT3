This confirms the analog: `create_mirror_puzzle()` is a fixed, well-known puzzle (`P2_PARENT.curry(Program.to(1))`) whose puzzle hash (`MIRROR_PUZZLE_HASH`) is not owner-specific. Any spend-bundle submitter can create a `CREATE_COIN` condition to this puzzle hash with arbitrary memo data — `memos[0]` becomes the `launcher_id` (any store id, not necessarily one the submitter owns), and `memos[1:]` become an arbitrary list of URL strings, as parsed by `get_mirror_info()`. [1](#0-0) 

Any node that has previously subscribed to / is tracking that `launcher_id` will, on seeing this coin, call `coin_added()` and unconditionally accept the mirror record via `is_launcher_tracked(launcher_id)` — with no check that the mirror's creator owns or is related to the store: [2](#0-1) 

Those attacker-supplied URLs are pulled into `DataLayer.update_subscriptions_from_wallet()` and then used directly as HTTP GET targets in `fetch_and_validate()` / `http_download()`: [3](#0-2) [4](#0-3) 

This is a genuine SSRF analog of the kdcproxy bug class: an unprivileged spend-bundle submitter supplies attacker-controlled destination data (URLs, analogous to the kdcproxy realm-driven SRV target) that is embedded in chain data, and a victim DataLayer node — reachable simply by tracking/subscribing to the targeted store id — will later make outbound HTTP requests to those attacker-chosen hosts/ports (including internal/loopback addresses) without the URL creator needing to own the store.

### Title
Unauthenticated coin-creation SSRF via forged DataLayer mirror coins - (File: chia/data_layer/data_layer_wallet.py)

### Summary
Any unprivileged spend-bundle submitter can create a coin paying to the fixed, well-known `MIRROR_PUZZLE_HASH` with a memo list encoding an arbitrary `launcher_id` (store id) and an arbitrary list of "mirror" URL strings. Any node tracking that `launcher_id` (subscriber or the owner itself) will ingest these attacker-chosen URLs as trusted mirror endpoints and later issue outbound HTTP requests to them from the DataLayer service, without the mirror creator needing any relationship to, or ownership of, the targeted store.

### Finding Description
`create_mirror_puzzle()` returns a fixed puzzle (`P2_PARENT.curry(Program.to(1))`) that is not parameterized by launcher id or owner key, so its tree hash `MIRROR_PUZZLE_HASH` is identical for every store on the network. [5](#0-4) 

`get_mirror_info()` parses `CREATE_COIN` conditions targeting this puzzle hash and treats `memos[0]` as the `launcher_id` and `memos[1:]` as URLs, with no signature check tying the memo content to the coin's actual spender/owner: [6](#0-5) 

`DataLayerWallet.coin_added()` is invoked for every incoming coin matching `MIRROR_PUZZLE_HASH`. It only checks `is_launcher_tracked(launcher_id)` — i.e., whether the local node happens to be tracking that store — before persisting the mirror with its attacker-supplied URL list: [2](#0-1) 

`DataLayer.update_subscriptions_from_wallet()` then reads these mirror URLs back from wallet RPC and stores them as subscription server URLs without any allow-list or ownership validation: [3](#0-2) 

Finally, `fetch_and_validate()` iterates these server URLs and issues an HTTP GET via `http_download()` directly against `server_info.url + "/" + filename`: [7](#0-6) [4](#0-3) 

This mirrors the kdcproxy CVE-2025-59088 pattern: an unauthenticated requester supplies a "realm"-like identifier (`launcher_id`) that causes the server to later resolve/connect to an attacker-influenced destination (mirror URL) it does not itself validate, exposing internal network topology and services.

### Impact Explanation
Any node running a DataLayer service and subscribed to (or owning) at least one store is reachable: an attacker submitting a single spend bundle that creates a `MIRROR_PUZZLE_HASH` coin can force that node's DataLayer process to make outbound HTTP requests to arbitrary attacker-chosen hosts and ports, including internal/loopback/private network addresses. This enables internal network/port scanning, probing firewall rules, and abuse of the requesting node as an SSRF proxy — consistent with the High severity of the analog CVE (network topology probing, port scanning, potential data exfiltration through response timing/behavior).

### Likelihood Explanation
Likelihood is high: creating a coin to a fixed, publicly known puzzle hash with attacker-chosen memo bytes requires no special privilege, keys, or ownership of the target store — only funds to pay the coin amount and fee. The victim need only be an existing subscriber/tracker of the targeted `launcher_id`, which is public information visible on-chain and via DataLayer RPCs.

### Recommendation
Bind mirror-coin authenticity to store ownership: reject or ignore mirror records whose creating coin's parent spend cannot be tied to the store's authorized wallet/owner key (e.g., require the mirror creation to be co-signed with, or announced by, the singleton owner), and validate/allow-list mirror URLs (scheme, host resolution against private/loopback ranges) before they are used as HTTP fetch targets in `fetch_and_validate()` / `http_download()`.

### Proof of Concept
1. Identify a `launcher_id` (DataLayer store id) that a target node is known to subscribe to or own (visible via public chain data / DataLayer RPC).
2. Construct and submit a spend bundle whose spend produces a `CREATE_COIN` condition to `MIRROR_PUZZLE_HASH` (`create_mirror_puzzle().get_tree_hash()`), with `amount` any dust value, and `memos = [launcher_id, b"http://169.254.169.254/latest/meta-data/", b"http://10.0.0.5:9999/"]`.
3. Once the spend confirms, the target node's `DataLayerWallet.coin_added()` parses the mirror coin via `get_mirror_info()` and stores it because `is_launcher_tracked(launcher_id)` is true. [2](#0-1) 
4. On the next `periodically_manage_data()` cycle, `update_subscriptions_from_wallet()` pulls these URLs into the subscription's server list, and `fetch_and_validate()`/`http_download()` issues outbound GET requests to the attacker-supplied hosts. [3](#0-2) [4](#0-3)

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

**File:** chia/data_layer/data_layer.py (L642-666)
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
