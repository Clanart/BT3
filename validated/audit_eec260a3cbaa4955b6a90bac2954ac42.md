### Title
DataLayer mirror-URL SSRF — any spend bundle can register an unvalidated attacker-controlled URL against an arbitrary store, causing the DataLayer service to make outbound HTTP requests to it - ([File: chia/data_layer/data_layer.py])

### Summary

### Finding Description
DataLayer mirror coins are the mechanism by which "helper" servers advertise where a store's delta/full-tree files can be downloaded from. A mirror coin is created by paying to a fixed, un-owned puzzle (`create_mirror_puzzle()` = `P2_PARENT.curry(Program.to(1))`, `MIRROR_PUZZLE_HASH`) and encoding `[launcher_id, *urls]` in the `CREATE_COIN` memo field: [1](#0-0) . `get_mirror_info()` simply reads whatever `launcher_id` and URL bytes are present in the memo of any `CREATE_COIN` to that puzzle hash — it does not check that the memoed `launcher_id` is owned, tracked, or controlled in any way by the spender: [2](#0-1) . The wallet-side `create_new_mirror()` helper likewise never validates that `launcher_id` belongs to the caller before emitting the coin: [3](#0-2) .

Because the memo (and therefore `launcher_id` + `urls`) is fully attacker-controlled data embedded in a coin creation, any party who can submit a spend bundle — not just the owner of the DataLayer singleton — can mint a mirror record that points at *someone else's* `store_id` with an arbitrary URL string, including `http://169.254.169.254/...`, `http://127.0.0.1:<internal-port>/...`, or any other SSRF target.

On the consuming side, `DataLayer.update_subscriptions_from_wallet()` pulls every mirror registered for a `store_id` from wallet RPC and adds all of their URLs as subscription servers without any allowlist, scheme check, or private/link-local IP filtering: [4](#0-3) . The periodic sync loop then randomly selects one of these servers and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which opens a real outbound HTTP connection from the DataLayer server process to that attacker-chosen URL, during `fetch_and_validate()`: [5](#0-4) . When no `downloader` plugin is configured, `download_file()` calls `http_download()` directly against `server_info.url`: [6](#0-5) ; when a plugin is configured, the attacker-controlled URL is instead forwarded as a JSON field (`"url": server_info.url`) to the local plugin process, which itself performs the outbound request: [7](#0-6) .

This mirrors the reported bug class precisely: an unauthenticated/low-privilege actor supplies an address string that a privileged backend service (webhook admission handler ↔ DataLayer sync loop) later dereferences with an outbound HTTP call, with no scheme/host validation and no ownership check tying the attacker-supplied identifier (`vault-addr`/`vault-serviceaccount` annotation ↔ mirror `launcher_id`/`urls` memo) to the entity that supplied it.

### Impact Explanation
Any coin spender (an "unprivileged spend-bundle submitter") can force any node that subscribes to a targeted DataLayer store to make arbitrary outbound HTTP requests from its DataLayer service process. This is a genuine SSRF: it can be used to probe/reach internal network services, cloud metadata endpoints, or other hosts unreachable from outside, using the victim node as a relay. It does not directly expose credentials the way the vault-secrets-webhook flaw does (there is no analogous SA-token exfiltration path here), but it satisfies "coin-set divergence"-adjacent and network-reachability impact criteria of the scope: it is a spend-triggered mechanism that causes a privileged process to make unauthenticated, unvalidated outbound network calls to attacker-chosen destinations. Because `launcher_id` is unauthenticated in the mirror-creation path, the blast radius is not limited to the attacker's own stores — any DataLayer store subscribed to by any node can be targeted.

### Likelihood Explanation
Exploitation requires only: (1) creating a coin with `puzzle_hash == MIRROR_PUZZLE_HASH` and a memo of `[launcher_id, url...]` — trivially constructible in a standard spend bundle for the cost of the coin amount + fee, and (2) a victim node running the DataLayer service that is subscribed to (or eventually subscribes to) the targeted `store_id`. No wallet RPC access to the victim is needed; the mirror record itself is on-chain data read by any syncing DataLayer node. Likelihood is Medium-High for any publicly-referenced/shared DataLayer store, since mirrors are attached to the store id, not gated by singleton ownership.

### Recommendation
- In `get_mirror_info()` / `create_new_mirror()` / `DataLayer.add_mirror()`, require that the `launcher_id` embedded in a mirror coin be validated against the actual DL singleton being tracked, or otherwise ensure mirror creation is scoped to the coin owner's own singleton (e.g., proof of ownership similar to `batch_insert()`'s owner check at [8](#0-7) ).
- Validate/allowlist mirror URLs before adding them as subscription servers in `update_subscriptions_from_wallet()` (reject non-http(s) schemes, private/link-local/loopback ranges, cloud metadata addresses) similar to standard SSRF mitigations.
- Consider treating mirror URLs purely as advisory/opt-in (require explicit user confirmation per new host) rather than automatically feeding them into the outbound download path in `fetch_and_validate()`.

### Proof of Concept
1. Attacker crafts a spend bundle whose only relevant output is a `CREATE_COIN` with:
   - `puzzle_hash = create_mirror_puzzle().get_tree_hash()` (i.e. `MIRROR_PUZZLE_HASH`)
   - `amount` = any small value the attacker is willing to spend
   - `memos = [victim_store_id, b"http://169.254.169.254/latest/meta-data/"]`
2. Submit the spend bundle to the mempool; once confirmed, `get_mirror_info()` will parse this as a legitimate `Mirror` record for `victim_store_id`, with no check that the attacker owns or tracks that launcher id.
3. Any node running `DataLayer` service that is subscribed to `victim_store_id` periodically calls `update_subscriptions_from_wallet(victim_store_id)` [4](#0-3) , adding `http://169.254.169.254/latest/meta-data/` as a candidate server.
4. On the next `fetch_and_validate()` cycle, that server may be selected and `insert_from_delta_file()` → `download_file()` → `http_download()` issues an outbound HTTP GET from the victim node's DataLayer process to the attacker-chosen address [9](#0-8) [10](#0-9) .

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

**File:** chia/data_layer/download_data.py (L127-145)
```python
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

**File:** chia/data_layer/download_data.py (L147-168)
```python
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
