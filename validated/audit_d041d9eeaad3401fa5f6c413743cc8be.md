## Title
SSRF via unauthenticated Data Layer mirror URLs causes arbitrary outbound HTTP requests from any subscribing node - (File: chia/data_layer/download_data.py)

### Summary
Chia's Data Layer "mirror" mechanism lets **any** wallet holder — with no relationship to a store's owner — publish an arbitrary URL on-chain tagged with an arbitrary `launcher_id`. Every other node that is subscribed to (or owns) that `launcher_id` automatically ingests this attacker-chosen URL as a download source and will later issue unauthenticated outbound HTTP `GET` requests to it. This is the same bug class as BIT-appsmith-2024-51408: a low-privilege actor supplies a URL that a trusted server process later fetches, enabling SSRF against internal/cloud-metadata endpoints (e.g. `169.254.169.254`) reachable from the victim node's host.

### Finding Description
`create_new_mirror()` builds a mirror coin using a generic, launcher-agnostic puzzle (`create_mirror_puzzle()` = `P2_PARENT.curry(Program.to(1))`) and encodes the target `launcher_id` and URL list purely as **memos** on a `CREATE_COIN` condition: [1](#0-0) 

Nothing ties the spending coin/wallet to ownership of `launcher_id` — any coin owner can mint a mirror coin for any store id and any URL and simply pay `amount + fee`: [2](#0-1) 

When a mirror coin is seen on chain, the wallet only checks whether **the local wallet is already tracking that `launcher_id`** (i.e., the node subscribes to or owns that store) — it does not check who created the mirror: [3](#0-2) 

The Data Layer service periodically pulls all mirrors for its subscribed/owned stores and feeds their URLs straight into local subscription state: [4](#0-3) 

`fetch_and_validate()` then randomly selects one of these server URLs and downloads a delta file from it with no host/scheme allow-listing: [5](#0-4) 

The actual network fetch, `http_download()`, does a raw `aiohttp` `GET` to `server_info.url + "/" + filename` with no validation that the URL is a legitimate mirror host, no blocking of link-local/loopback/private ranges, and no restriction on scheme beyond whatever `aiohttp` accepts: [6](#0-5) 

Contrast this with the S3 plugin path, which does restrict scheme and validates the URL against a locally configured allow-list before downloading: [7](#0-6) 

No equivalent allow-list/URL validation exists for the default HTTP mirror path.

### Impact Explanation
Any unprivileged party who can broadcast a cheap spend bundle (minimal XCH amount + fee) can force every Data Layer node that subscribes to a known, public `launcher_id` to make outbound HTTP requests to an attacker-chosen destination — including cloud metadata services (`169.254.169.254`), internal admin panels, or other services reachable only from the victim's network. This mirrors the AppSmith SSRF exactly: a data-source/URL field intended for legitimate use is abused to make the server fetch attacker-controlled endpoints, risking credential/metadata disclosure and internal network reconnaissance from any node running the Data Layer service (a normal component of Chia full node/wallet operation for anyone using Data Layer stores).

### Likelihood Explanation
Likelihood is high for any node operator who runs the Data Layer service and subscribes to or owns any store: `launcher_id`s are public on-chain values, mirror coins can be created by anyone for a trivial fee, and `periodically_manage_data()` automatically pulls and acts on all mirrors for tracked launchers without additional authorization or ownership checks. No special conditions besides "operate Data Layer and track/own a store" are required to be targeted.

### Recommendation
- Restrict mirror creation so a mirror for `launcher_id` can only be recorded if it was created by (or otherwise validated against) the actual owner/singleton of that store, not by an arbitrary coin spend with attacker-chosen memos.
- Validate mirror/subscription URLs before use: enforce an explicit scheme allow-list (e.g. `http`/`https` only), and block resolution to loopback, link-local (`169.254.0.0/16`), and other private/internal address ranges before issuing `aiohttp` requests in `http_download()`.
- Consider requiring explicit user opt-in/allow-listing of mirror hosts rather than trusting on-chain mirror URLs implicitly for outbound network fetches.

### Proof of Concept
1. Attacker identifies a public `launcher_id` known to be tracked by the victim's Data Layer node (store ids/launcher ids are public on-chain).
2. Attacker calls `dl_new_mirror` (or the CLI `chia data add_mirror`) with that `launcher_id`, a nominal `amount`, and `urls=["http://169.254.169.254/latest/meta-data/iam/security-credentials/"]`, and gets it confirmed on chain — no relationship to store ownership is required, per `create_new_mirror()`/`create_mirror_puzzle()`.
3. Victim's Data Layer node, already tracking that `launcher_id`, ingests the mirror via `coin_added()`/`update_subscriptions_from_wallet()` since it only checks local tracking, not mirror authorship.
4. During the next sync cycle, `fetch_and_validate()` selects the malicious server and `http_download()` issues an outbound `GET` to the attacker-supplied URL from the victim's host, with no scheme/host restriction. [6](#0-5)

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

**File:** chia/data_layer/data_layer.py (L642-690)
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

**File:** chia/data_layer/s3_plugin_service.py (L257-281)
```python
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
```
