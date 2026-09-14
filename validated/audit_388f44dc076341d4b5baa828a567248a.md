### Title
DataLayer mirror URLs are unvalidated attacker-controlled strings that trigger outbound HTTP requests from any subscribing wallet's DataLayer service - (File: chia/data_layer/data_layer_wallet.py, chia/data_layer/data_layer.py)

### Summary
The OpenProject report describes an ID field (work-package ID) that was not validated to be numeric before being used to build a URL for an automatic `GET` request, letting an attacker embed arbitrary paths/hosts that get requested when a victim merely opens a document. The analogous pattern in this codebase is Chia's DataLayer "mirror" mechanism: any coin spender can create a mirror coin carrying arbitrary URL strings in its memos, and other users' DataLayer services will later read those strings and use them to build outbound HTTP requests, without validating that the value is a well-formed/expected URL.

### Finding Description
When a mirror coin is created and spent, `get_mirror_info()` extracts the launcher id and a list of raw memo bytes and treats them directly as URLs with no format validation: [1](#0-0) 

`DataLayerWallet.coin_added()` picks up any coin sent to the mirror puzzle hash, decodes the memos via `get_mirror_info`, and stores them into `dl_store` as tracked mirrors for the corresponding `launcher_id`, again with no validation that the launcher_id is actually associated with the store being watched by this wallet or that the URL strings are safe/well-formed: [2](#0-1) 

These mirror URLs subsequently flow into `DataLayer.update_subscriptions_from_wallet()`, which pulls `dl_get_mirrors` output and writes the raw URL strings into `DataStore` subscriptions: [3](#0-2) 

Finally, `fetch_and_validate()` iterates these attacker-influenced URLs and issues outbound network requests (delta-file downloads / plugin `handle_download` POSTs) directly against them: [4](#0-3) [5](#0-4) 

The project's own internal documentation acknowledges this is a known trust boundary that must stay strict, explicitly calling out that "Plugin and mirror URLs are external trust inputs" and that data from them "must continue to be verified against wallet-advertised roots": [6](#0-5) 

I was not able to locate the mirror-creation RPC path (`create_new_mirror` / `dl_new_mirror` handler) in the indexed portion of `chia/data_layer/data_layer_wallet.py` to confirm whether any URL scheme/format validation exists there before the coin is spent on-chain — this may be present but outside what the index returned. However, the consumption side (`get_mirror_info`, `coin_added`, `fetch_and_validate`, `get_downloader`) demonstrably treats memo-derived strings as URLs without validation, and since mirror memos are attacker-controlled data embedded in a spend bundle that anyone can broadcast, the trust boundary crossed here is analogous to the OpenProject bug: an ID/parameter sourced from untrusted user input is used unvalidated to construct a network request that fires automatically for other participants (here, any wallet subscribed to or syncing that DataLayer store).

### Impact Explanation
Any user able to submit a spend bundle can create a mirror coin with the mirror puzzle and arbitrary memo bytes for any `launcher_id` (a DataLayer store id). If a victim wallet is tracking/subscribed to that store, its DataLayer service will treat the attacker's memo strings as server URLs and automatically make outbound HTTP requests to them during the periodic `fetch_and_validate` / `update_subscription` loop, and pass them to `handle_download` plugin POST requests. This is a forced-request / SSRF-style condition analogous to the OpenProject finding: unvalidated attacker-supplied identifiers driving automatic outbound requests from another party's service, which can be used for internal network probing, resource exhaustion against arbitrary hosts, or interacting with internal-only services reachable from the DataLayer host — all without the victim's explicit consent, satisfying the "Forced Action" class described in the source report.

### Likelihood Explanation
Moderate-to-high: creating a mirror coin only requires being able to spend a coin with the mirror puzzle and controlling CREATE_COIN memos, which is achievable by any unprivileged user running a wallet with DataLayer/mirror support (or crafting the puzzle solution directly), and no special privilege over the target store is required for the mirror to be recorded as long as the `launcher_id` matches a store the victim already tracks. Actual exploitation impact (what unsafe schemes are reachable, e.g. `file://`, internal IPs) depends on the HTTP client library behavior (`aiohttp.ClientSession`), which was not fully auditable here for scheme/host restrictions.

### Recommendation
Validate mirror URLs before persisting/using them: enforce an allow-list of schemes (`http://`/`https://` only), reject loopback/link-local/internal IP ranges unless explicitly configured, and bound the URL length/character set at the point they are extracted in `get_mirror_info()` and again before use in `DataLayer.fetch_and_validate()` / `get_downloader()`. Consider also validating that the `launcher_id` embedded in mirror memos actually corresponds to an existing/expected singleton before trusting associated URLs in `coin_added()`.

### Proof of Concept
Not independently reproduced in this environment (no execution/sandbox access). Conceptually: attacker crafts a spend of a coin to `create_mirror_puzzle()`'s puzzle hash with `CREATE_COIN` memos `[launcher_id, b"http://169.254.169.254/latest/meta-data/", ...]` targeting a `launcher_id` of a store a victim node subscribes to; once confirmed, the victim's `DataLayer.update_subscription()` / `fetch_and_validate()` loop will attempt HTTP requests against that attacker-chosen URL as part of normal mirror-sync behavior.

### Citations

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

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
