## SSRF Analog Found: Attacker-Controlled Mirror URLs Fetched by Data Layer Without Address/Scheme Validation

### Title
Server-Side Request Forgery via Attacker-Supplied DataLayer Mirror URLs in `http_download()` - (File: `chia/data_layer/download_data.py`)

### Summary
Chia's Data Layer service resolves peer "mirror" URLs for a subscribed/owned store directly from on-chain mirror coins and, without any scheme, redirect, or private/loopback/link-local address validation, issues HTTP requests to those URLs to download delta/full tree files. Anyone able to spend a coin on-chain (an unprivileged party, no special permission) can create such a mirror coin with attacker-chosen `urls` memos for *any* launcher_id, and if the victim node tracks or owns that store, its Data Layer background sync loop will fetch attacker-chosen URLs, including internal-network, loopback, or cloud-metadata addresses — the same bug class as CVE-2026-62857 (Fedify `getNodeInfo()` SSRF via unvalidated `href`).

### Finding Description
Any coin that pays to the mirror puzzle hash (`create_mirror_puzzle()`) and encodes a `launcher_id` and list of `urls` in its memos is recognized by the wallet as a "mirror" for that launcher, with the only guard being that the launcher id is already tracked/owned locally: [1](#0-0) 

`get_mirror_info()` simply extracts `launcher_id` and raw `urls` from the spend's memos with no validation of URL content: [2](#0-1) 

The Data Layer service periodically pulls these wallet-tracked mirror URLs and merges them into its subscription server list for the corresponding store: [3](#0-2) [4](#0-3) 

The background sync loop (`periodically_manage_data()` → `update_subscription()`) runs this merge and then fetches from those URLs for every subscription, including pseudo-subscriptions for stores the node merely owns: [5](#0-4) 

`fetch_and_validate()` randomly shuffles the server list (which now includes the attacker's URL) and passes the chosen `server_info.url` straight into the download path: [6](#0-5) 

Finally, `http_download()` builds the request target by direct string concatenation of the attacker-supplied URL and issues the HTTP GET with no scheme allow-list and no check against loopback/link-local/private/cloud-metadata address ranges: [7](#0-6) 

This mirrors the Fedify bug class exactly: an externally-supplied `href`/URL value is followed by an outbound HTTP client with no validation of scheme, redirect target, or destination address space, and the response (status, size, timing, partial content before hash-verification fails) is observable by the party who can trigger new spends including this attacker.

### Impact Explanation
A remote, fully unprivileged actor (anyone who can submit a standard coin spend on-chain) can force any Chia full-node/wallet operator running the Data Layer service and tracking (subscribing to or owning) a given store to make outbound HTTP requests to attacker-chosen destinations — e.g. `http://127.0.0.1:<port>/...`, `http://169.254.169.254/latest/meta-data/...`, or internal RPC/service endpoints reachable only from the victim's network. Because DataLayer nodes are often run alongside wallet/full-node RPC services and sometimes in cloud environments, this can be used to probe internal network topology, hit local unauthenticated RPCs, or query cloud instance-metadata services, and observe success/failure/size via node behavior differences and logs. This is a Medium-severity SSRF impacting confidentiality/availability of internal resources reachable from the victim host, matching the reachable-analog scope (Data Layer client) called out in the rules.

### Likelihood Explanation
Likelihood is high for any operator running Data Layer and subscribed to (or owning) a publicly known store id: creating the malicious mirror coin only requires a standard spend paying the mirror puzzle hash with crafted memos, which any wallet holder can construct and broadcast; no special privilege, mempool bypass, or protocol-level trust is required. The victim's periodic sync loop automatically merges and fetches from these URLs without operator interaction.

### Recommendation
Before performing any outbound request in `http_download()` (and the analogous `get_downloader()`/plugin `download`/`handle_download` POST paths), validate mirror/server URLs: restrict to an explicit scheme allow-list (e.g. `http`/`https`), resolve the hostname and reject loopback, link-local, private (RFC1918), and other non-public/reserved address ranges (including cloud metadata addresses), and re-validate after any redirect. Consider requiring operator opt-in/allow-listing of mirror hosts, and treat `Mirror.urls` as untrusted input at the point they are merged into `DataStore.update_subscriptions_from_wallet()` as well, not only at request time.

### Proof of Concept
1. Attacker crafts and broadcasts a standard spend that creates a coin with puzzle hash `create_mirror_puzzle().get_tree_hash()`, with memos encoding a target `launcher_id` equal to a store the victim tracks/owns, and `urls = ["http://169.254.169.254"]` (or `http://127.0.0.1:<internal-port>`).
2. Victim's wallet `coin_added()` in `chia/data_layer/data_layer_wallet.py` sees the mirror coin, confirms the launcher is tracked, and stores the mirror in `dl_wallet_store`.
3. Victim's Data Layer service, in its next `periodically_manage_data()` cycle, calls `update_subscriptions_from_wallet()`, pulling the attacker's URL into the store's subscription server list (`chia/data_layer/data_layer.py:979-985`).
4. `fetch_and_validate()` selects that server (random shuffle) and calls `insert_from_delta_file()` → `download_file()` → `http_download()`, which issues `session.get("http://169.254.169.254/" + filename, ...)` from the victim host (`chia/data_layer/download_data.py:298-324`), demonstrating outbound SSRF to an internal/metadata address chosen entirely by the attacker.

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

**File:** chia/data_layer/data_layer.py (L1006-1116)
```python
    async def periodically_manage_data(self) -> None:
        manage_data_interval = self.config.get("manage_data_interval", 60)
        while not self._shut_down:
            # Add existing subscriptions
            async with self.subscription_lock:
                subscriptions = await self.data_store.get_subscriptions()

            # Track each subscription individually so one bad subscription can't block the
            # others or entry into the rest of the management cycle.
            await self.track_subscriptions(subscriptions)

            # pseudo-subscribe to all unsubscribed owned stores
            # Need this to make sure we process updates and generate DAT files
            try:
                owned_stores = await self.get_owned_stores()
            except (ValueError, aiohttp.client_exceptions.ClientConnectorError):
                # Sometimes the DL wallet isn't available, so we can't get the owned stores.
                # We'll try again next time.
                owned_stores = []
            except Exception as e:
                self.log.error(f"Exception while fetching owned stores: {type(e)} {e} {traceback.format_exc()}")
                owned_stores = []

            subscription_store_ids = {subscription.store_id for subscription in subscriptions}
            for record in owned_stores:
                store_id = record.launcher_id
                if store_id not in subscription_store_ids:
                    try:
                        # don't actually subscribe, just add to the list
                        subscriptions.insert(0, Subscription(store_id=store_id, servers_info=[]))
                    except Exception as e:
                        self.log.info(
                            f"Can't subscribe to owned store {store_id}: {type(e)} {e} {traceback.format_exc()}"
                        )

            # Optionally
            # Subscribe to all local non-owned store_ids that we can find on chain.
            # This is the prior behavior where all local stores, both owned and not owned, are subscribed to.
            if self.config.get("auto_subscribe_to_local_stores", False):
                local_store_ids = await self.data_store.get_store_ids()
                subscription_store_ids = {subscription.store_id for subscription in subscriptions}
                for local_id in local_store_ids:
                    if local_id not in subscription_store_ids:
                        try:
                            subscription = await self.subscribe(local_id, [])
                            subscriptions.insert(0, subscription)
                        except Exception as e:
                            self.log.info(
                                f"Can't subscribe to local store {local_id}: {type(e)} {e} {traceback.format_exc()}"
                            )

            work_queue: asyncio.Queue[Job[Subscription]] = asyncio.Queue()
            async with QueuedAsyncPool.managed(
                name="DataLayer subscription update pool",
                worker_async_callable=self.update_subscription,
                job_queue=work_queue,
                target_worker_count=self.subscription_update_concurrency,
                log=self.log,
            ):
                jobs = [Job(input=subscription) for subscription in subscriptions]
                for job in jobs:
                    await work_queue.put(job)

                await asyncio.gather(*(job.done.wait() for job in jobs), return_exceptions=True)

            # Do unsubscribes after the fetching of data is complete, to avoid races.
            async with self.subscription_lock:
                still_pending: list[UnsubscribeData] = []
                for unsubscribe_data in self.unsubscribe_data_queue:
                    try:
                        await self.process_unsubscribe(unsubscribe_data.store_id, unsubscribe_data.retain_data)
                    except Exception as e:
                        # A single failing unsubscribe (e.g. the wallet is unreachable) must not
                        # kill the loop or block the others; retry it on the next cycle.
                        self.log.warning(
                            f"Exception while processing queued unsubscribe for "
                            f"{unsubscribe_data.store_id}: {type(e)} {e}"
                        )
                        still_pending.append(unsubscribe_data)
                self.unsubscribe_data_queue[:] = still_pending
            await asyncio.sleep(manage_data_interval)

    async def track_subscriptions(self, subscriptions: list[Subscription]) -> None:
        for subscription in subscriptions:
            try:
                await self.wallet_rpc.dl_track_new(DLTrackNew(launcher_id=subscription.store_id))
            except aiohttp.client_exceptions.ClientConnectorError as e:
                # Wallet unreachable: retry the remaining subscriptions on the next cycle.
                self.log.warning(f"Cannot connect to the wallet to track subscriptions ({e}). Retrying next cycle.")
                return
            except Exception as e:
                # One subscription failing to track must not abort tracking of the others.
                self.log.warning(
                    f"Exception while requesting wallet track subscription {subscription.store_id}: {type(e)} {e}"
                )

    async def update_subscription(
        self,
        worker_id: int,
        job: Job[Subscription],
    ) -> None:
        subscription = job.input

        try:
            await self.update_subscriptions_from_wallet(subscription.store_id)
            await self.fetch_and_validate(subscription.store_id)
            await self.upload_files(subscription.store_id)
            await self.clean_old_full_tree_files(subscription.store_id)
        except Exception as e:
            self.log.error(f"Exception while fetching data: {type(e)} {e} {traceback.format_exc()}.")

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
