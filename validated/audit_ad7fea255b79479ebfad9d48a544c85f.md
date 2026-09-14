## Title
Unauthenticated on-chain SSRF via DataLayer mirror coin URLs — (File: chia/data_layer/data_layer_wallet.py)

### Summary
Any chain participant can create a "mirror" coin that carries an arbitrary `launcher_id` (any DataLayer store, not necessarily one they own) plus an arbitrary list of URL strings in its memo. `DataLayerWallet.coin_added` accepts and persists these attacker-chosen URLs for any tracked store without validating scheme, host, or ownership. Those URLs are later loaded as subscription mirror servers and fed straight into an unrestricted `aiohttp` HTTP GET, giving any spend-bundle submitter the ability to force other nodes/wallets that track the same store id to issue outbound requests to attacker-chosen destinations — the same bug class (CWE-918 SSRF) as CVE-2020-17513.

### Finding Description
A "mirror" coin is any coin sent to `create_mirror_puzzle()`'s puzzle hash, carrying a memo of `[launcher_id, url1, url2, ...]`: [1](#0-0) 

When such a coin is observed on-chain, `DataLayerWallet.coin_added` decodes it via `get_mirror_info` and, as long as the referenced `launcher_id` is locally tracked (i.e. the node subscribes to or owns that DataLayer store — a completely normal, expected condition for any node participating in that store), stores the mirror record with its attacker-supplied `urls` with no validation of scheme, host, or that the sender owns/controls that store: [2](#0-1) 

`DataLayerStore.add_mirror` persists these URL strings verbatim: [3](#0-2) 

`DataLayer.update_subscriptions_from_wallet` pulls every mirror URL for the store straight from wallet RPC and registers them as subscription server candidates, again with no allowlist/validation: [4](#0-3) 

The periodic sync loop (`fetch_and_validate`) picks a mirror URL and calls `insert_from_delta_file` → `download_file` → `http_download`, which performs a plain `aiohttp` GET against `server_info.url` with no restriction on target host/IP (no blocking of loopback, link-local/metadata addresses, or private ranges): [5](#0-4) [6](#0-5) 

The module's own documentation acknowledges mirror URLs are untrusted input but the trust boundary is only enforced on the *downloaded content* (validated against the wallet-advertised Merkle root), not on the *request destination itself*: [7](#0-6) [8](#0-7) 

### Impact Explanation
Any wallet user willing to spend a trivial amount (mirror coin amount + fee) can submit a standard, valid spend bundle that publishes a mirror coin naming an arbitrary `launcher_id` for any DataLayer store already tracked by victim nodes, with URLs pointing at internal-only endpoints (e.g. `http://127.0.0.1:<port>/...`, cloud metadata IPs, or arbitrary internal hosts). Every node/wallet running a DataLayer service that tracks that store will then have its DataLayer background sync loop issue outbound HTTP GET requests to the attacker-chosen destination — a textbook SSRF (CWE-918), matching CVE-2020-17513's bug class. This can be used to probe internal network topology/services reachable from the victim host, trigger requests against internal-only services, or induce unwanted outbound traffic at scale from many nodes simultaneously, without requiring the attacker to own or control the targeted store.

### Likelihood Explanation
Likelihood is high: publishing a mirror coin only requires a standard, unprivileged transaction with a small coin amount and standard fee — no special permission, ownership of the store, or privileged RPC access is required. Any node that subscribes to/tracks the targeted `launcher_id` (a normal, encouraged DataLayer usage pattern for public stores) is automatically affected the next time its periodic `periodically_manage_data()`/`update_subscription()` cycle runs.

### Recommendation
- Validate mirror URLs before persisting/using them: enforce an allowed scheme list (e.g. `http`/`https`/`s3` only) and reject/resolve-check hosts that resolve to loopback, link-local (including cloud metadata ranges like `169.254.169.254`), or other private/reserved address ranges unless explicitly configured (similar to SSRF mitigations added in the Airflow fix).
- Consider requiring mirror URLs to be associated with stores the local node actually owns before auto-subscribing to them, or make mirror-derived subscription URLs opt-in per store rather than automatic.
- Apply the same destination validation to plugin/downloader URLs (`get_downloader`, `download_file` plugin path) since they follow the same untrusted-input pattern.

### Proof of Concept
1. Attacker crafts and submits (via the standard wallet `dl_new_mirror`/mirror-coin construction path, or directly as a spend bundle) a coin with puzzle hash `create_mirror_puzzle().get_tree_hash()` and memo `[victim_tracked_launcher_id, b"http://169.254.169.254/latest/meta-data/"]`, paying a minimal amount + fee.
2. Once confirmed, any victim node whose `DataLayerWallet` tracks `victim_tracked_launcher_id` (e.g. because it subscribes to that public DataLayer store) processes the coin in `coin_added` (`chia/data_layer/data_layer_wallet.py:775-800`) and records the mirror via `DataLayerStore.add_mirror` (`chia/data_layer/dl_wallet_store.py:308-325`).
3. On the victim's next `periodically_manage_data()` cycle, `update_subscriptions_from_wallet` (`chia/data_layer/data_layer.py:979-985`) loads the malicious URL as a subscription server, and `fetch_and_validate`/`insert_from_delta_file`/`download_file`/`http_download` (`chia/data_layer/data_layer.py:668-694`, `chia/data_layer/download_data.py:298-319`) issues an outbound `aiohttp` GET to the attacker-controlled internal URL from the victim's machine.

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

**File:** chia/data_layer/dl_wallet_store.py (L308-325)
```python
    async def add_mirror(self, mirror: Mirror) -> None:
        """
        Add a mirror coin to the DB
        """

        async with self.db_wrapper.writer_maybe_transaction() as conn:
            await conn.execute_insert(
                "INSERT OR REPLACE INTO mirrors VALUES (?, ?, ?, ?, ?)",
                (
                    mirror.coin_id,
                    mirror.launcher_id,
                    mirror.amount.stream_to_bytes(),
                    b"".join(
                        [uint16(len(url)).stream_to_bytes() + url for url in Mirror.encode_urls(mirror.urls)]
                    ),  # prefix each item with a length
                    1 if mirror.ours else 0,
                ),
            )
```

**File:** chia/data_layer/data_layer.py (L668-694)
```python
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

**File:** .cursor/context/data-layer.md (L27-27)
```markdown
- Do not treat mirror URLs, plugins, or static file names as trusted data sources.
```

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
