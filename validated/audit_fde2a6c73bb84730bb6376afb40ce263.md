### Title
Unauthenticated on-chain SSRF via DataLayer mirror coin URLs consumed by wallet/DataLayer HTTP downloader - (File: chia/data_layer/download_data.py)

### Summary
Chia's DataLayer mirror mechanism lets any wallet create a "mirror coin" that advertises arbitrary URLs for an arbitrary `launcher_id` (store id). Anyone who is spending on the network can construct a coin spend to `MIRROR_PUZZLE_HASH` with memos of their choosing, since the mirror puzzle is a generic anyone-can-create puzzle keyed only by memo content, not by ownership of the referenced store's singleton. Those attacker-chosen URLs are later read out of chain state by any node/wallet that is tracking or subscribed to that store, and fed directly into `http_download()`/`download_file()` as the target for an outbound `aiohttp` GET request, with no scheme/host allow-listing (no protection against `file://`, loopback, link-local metadata addresses, or internal RFC1918 hosts). This mirrors the underlying bug class in CVE-2021-43780: an "untrusted data source URL" is trusted at the point of an outbound fetch, enabling SSRF against the fetching node's local network.

### Finding Description
`create_mirror_puzzle()` returns `P2_PARENT.curry(Program.to(1))`, a generic anyone-can-spend-into puzzle [1](#0-0) . Any spend bundle can create a coin at `MIRROR_PUZZLE_HASH` and set the `CREATE_COIN` memos to `[launcher_id, *urls]`; `get_mirror_info()` blindly parses those memos to recover the `launcher_id` and URL list, with no cryptographic or on-chain binding proving the spender controls the singleton at `launcher_id` [2](#0-1) .

The legitimate creation path (`DataLayerWallet.create_new_mirror`) shows exactly this construction pattern is intended, but nothing in `get_mirror_info` re-validates that the coin's memo-embedded `launcher_id` corresponds to a store actually owned/authorized by that spend [3](#0-2) .

Once such a mirror coin exists on-chain, any wallet/DataLayer node tracking that `launcher_id` will pull the (attacker-controlled) mirror URLs from wallet RPC (`dl_get_mirrors`) and feed them into `update_subscriptions_from_wallet()`, which stores them as subscription server URLs without validation [4](#0-3) . The DataLayer service's periodic sync (`fetch_and_validate()`) then iterates these server URLs and calls `insert_from_delta_file()` / `download_file()` using the attacker-supplied URL as the fetch target [5](#0-4) . `download_file()` passes the URL straight to `http_download()`, which opens an `aiohttp` session and issues `session.get(server_info.url + "/" + filename, ...)` with no restriction on scheme or destination host/IP [6](#0-5) .

The Data Layer module notes explicitly acknowledge mirror/plugin URLs are untrusted inputs for data content, but the trust model only covers validating the downloaded *data* against the wallet-advertised Merkle root — it does not address the SSRF risk of the outbound *request itself* reaching internal-only endpoints [7](#0-6) [8](#0-7) .

### Impact Explanation
Any unprivileged chia network participant (a normal wallet user who can submit a spend bundle) can trigger unsolicited, attacker-directed outbound HTTP(S) requests from any other user's local machine that is subscribed to or tracking the targeted DataLayer store — including requests to `localhost`, cloud metadata endpoints (`169.254.169.254`), or other internal-network services reachable from the victim's host, since the DataLayer server/downloader process typically runs alongside other locally-bound services (full node RPC, wallet RPC, DataLayer's own static file server, S3 plugin services). This is a classic SSRF condition and can be used for internal network reconnaissance, port scanning, or interacting with unauthenticated local services exposed only to loopback/internal interfaces. It does not directly cause loss of funds or consensus divergence, but it is a genuine confidentiality/integrity-impacting network-reachable vulnerability triggerable purely by posting a coin spend, matching the "High" severity class of the referenced SSRF CVE (data-source URL fetch without egress restriction).

### Likelihood Explanation
Likelihood is high for any node that is actively subscribed to or auto-tracking third-party DataLayer stores (a normal, encouraged DataLayer usage pattern for mirroring/replication) since the mirror coin creation path requires no special permission — only a standard XCH spend to the mirror puzzle hash with attacker-chosen memos, which any wallet can construct and broadcast.

### Recommendation
- In `get_mirror_info()`/mirror-coin processing, do not treat memo-derived `launcher_id`/URL pairs as globally valid; require callers to only honor mirrors that a locally-verified singleton/DID/authority explicitly opted into, or otherwise bind mirror authorization to the singleton owner (e.g., require the mirror coin's creation to be signed/attested by the current owner's key rather than free-form memos).
- Apply strict URL/host validation before any outbound fetch in `http_download()`/`download_file()`/`get_downloader()`: enforce `http`/`https` scheme only, resolve and reject loopback, link-local, and RFC1918 private ranges (unless explicitly opted-in via config), and disallow redirects to disallowed destinations.
- Document and enforce that mirror/plugin URLs are an untrusted network-reachable surface, and add the SSRF mitigation alongside the existing "downloaded data must be verified against wallet-advertised roots" trust boundary described in the DataLayer notes.

### Proof of Concept
1. Attacker constructs and broadcasts a standard coin spend that creates a coin with `puzzle_hash = create_mirror_puzzle().get_tree_hash()` (`MIRROR_PUZZLE_HASH`), setting `CREATE_COIN` memos to `[victim_store_launcher_id, b"http://169.254.169.254/latest/meta-data/", b"http://127.0.0.1:8562/some-admin-path"]` — this mirrors the exact memo shape produced by `DataLayerWallet.create_new_mirror` [9](#0-8) , requiring no relationship to `victim_store_launcher_id`'s true owner.
2. The spend confirms on-chain; any victim node tracking `victim_store_launcher_id` picks up the new mirror via `dl_get_mirrors` and folds the URLs into its subscription set through `update_subscriptions_from_wallet()` [4](#0-3) .
3. On the victim's next `fetch_and_validate()` cycle, the DataLayer service iterates its subscribed servers and calls `insert_from_delta_file()`/`download_file()` against the attacker's URL [10](#0-9) , and `http_download()` issues `session.get(url + "/" + filename, ...)` from the victim's process to that attacker-chosen internal endpoint [11](#0-10) , completing the SSRF.

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L90-94)
```python
def create_mirror_puzzle() -> Program:
    return P2_PARENT.curry(Program.to(1))


MIRROR_PUZZLE_HASH = create_mirror_puzzle().get_tree_hash()
```

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L97-110)
```python
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

**File:** chia/data_layer/download_data.py (L298-319)
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
```

**File:** .cursor/context/data-layer.md (L26-28)
```markdown
- Do not treat a local root as current chain truth until wallet confirmation status has been reconciled.
- Do not treat mirror URLs, plugins, or static file names as trusted data sources.
- Do not collapse `None`, omitted root fields, and empty-root sentinels across RPC/service/wallet boundaries.
```

**File:** .cursor/context/data-layer.md (L87-88)
```markdown
- Empty-root normalization is subtle and historically uneven. Audit any change touching `bytes32.zeros`, `None`, `Root.node_hash`, or proof roots with both empty and non-empty stores.
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
