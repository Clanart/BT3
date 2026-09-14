## Title
Server-Side Request Forgery via unauthenticated on-chain DataLayer mirror URLs auto-fetched by `fetch_and_validate`/`http_download` - (File: `chia/data_layer/data_layer.py`, `chia/data_layer/download_data.py`, `chia/wallet/wallet_rpc_api.py`)

### Summary
Any wallet holder can create a "mirror" coin for **any** DataLayer store (identified only by its public `launcher_id`) via the `dl_new_mirror` RPC/CLI, embedding arbitrary attacker-chosen URLs as coin memos. Any DataLayer node that owns or subscribes to that store will pick up these on-chain-published URLs and automatically issue outbound HTTP requests to them with no scheme/host validation, resulting in authenticated (via any funded wallet) Server-Side Request Forgery — directly analogous to the AVideo `downloadURL` SSRF (CWE-918).

### Finding Description
`DLNewMirror`/`dl_new_mirror` lets a caller create a mirror coin for an arbitrary `launcher_id` with attacker-controlled `urls`, with no check that the caller owns or controls that singleton: [1](#0-0) 

The wallet stores mirrors for any store it is "tracking" (owns or subscribes to) purely by matching the puzzle hash of the mirror coin, without validating the origin of the URLs: [2](#0-1) 

`DataLayer.update_subscriptions_from_wallet` pulls these mirror URLs straight from the wallet and feeds them into the local subscription table as candidate download servers, without any allow-listing, scheme restriction, or private/internal-address filtering: [3](#0-2) 

`fetch_and_validate` then iterates these server URLs and calls `insert_from_delta_file` → `download_file` → `http_download`, which performs a plain `aiohttp` GET to `server_info.url + "/" + filename` on the node's behalf: [4](#0-3) [5](#0-4) 

There is no validation that the URL points to a legitimate public mirror server (no blocklist for loopback, link-local/cloud-metadata, or RFC1918 addresses, and no scheme restriction beyond what `aiohttp` itself supports). The `.cursor/context/data-layer.md` notes explicitly acknowledge that "Plugin and mirror URLs are external trust inputs," underscoring that URL trust is presumed rather than enforced in code: [6](#0-5) 

### Impact Explanation
An attacker who knows (or discovers on-chain) the `launcher_id` of any DataLayer store can spend a trivial amount of XCH to publish a mirror coin whose memo contains an internal/attacker-chosen URL (e.g., `http://169.254.169.254/latest/meta-data/`, `http://127.0.0.1:<internal-rpc-port>/...`, or an internal-network host). Any node that owns or subscribes to that store will subsequently perform an outbound HTTP GET to that URL as part of routine `fetch_and_validate`/`update_subscription` cycling, without the store owner initiating or approving that specific destination. This can be used to probe internal network topology, reach cloud metadata endpoints, or interact with internal services reachable from the victim's host — a genuine authenticated SSRF (CWE-918), matching the CVSS vector of the referenced AVideo advisory (network-reachable, low complexity, requires only low-privilege authentication, confidentiality/integrity impact via internal network access).

### Likelihood Explanation
Likelihood is high for any actively used DataLayer store: `launcher_id`s are public on-chain data, mirror creation costs only a small transaction fee plus the mirror-coin amount (attacker-controlled, can be minimal), and `update_subscription()`/`fetch_and_validate()` run automatically as part of normal DataLayer service operation for owned and subscribed stores — no manual approval step exists for individual mirror URLs before they are dialed.

### Recommendation
- In `dl_new_mirror`/`DataLayerWallet.create_new_mirror`, and/or when consuming mirrors in `update_subscriptions_from_wallet`/`fetch_and_validate`, validate and restrict mirror/server URLs: enforce `https`/`http` scheme allow-list, resolve and reject loopback/link-local/private/multicast/metadata-service IP ranges, and/or require operator opt-in (allow-list) before a discovered mirror URL is added as an auto-fetch target.
- Consider requiring mirrors to be explicitly approved by the store owner/operator (e.g., via config) rather than automatically trusting any on-chain mirror coin for a tracked launcher_id.
- Apply the same URL validation to plugin `downloader`/`uploader` "handle_download" POST targets in `get_downloader`, since those too originate from configuration/mirror data.

### Proof of Concept
1. Attacker identifies the public `launcher_id` of a victim's DataLayer store (e.g., observed on chain or via `dl_owned_singletons`/subscription discovery).
2. Attacker calls `dl_new_mirror` (via wallet RPC/CLI `chia data add_mirror` or directly through `DLNewMirror`) supplying `launcher_id=<victim_store_id>`, `amount=1`, `urls=["http://169.254.169.254"]`, and a small fee, then pushes the resulting spend bundle to the mempool.
3. Once confirmed, the victim's wallet observes the mirror coin (`coin_added` in `chia/data_layer/data_layer_wallet.py:775-800`) and records the attacker URL as a mirror for the tracked `launcher_id`.
4. On the victim's next `update_subscription`/`fetch_and_validate` cycle, `chia/data_layer/data_layer.py:979-985` and `chia/data_layer/download_data.py:298-323` cause the victim's DataLayer service to issue an outbound HTTP GET to `http://169.254.169.254/<filename>`, demonstrating attacker-triggered SSRF from the victim's node.

### Citations

**File:** chia/wallet/wallet_rpc_api.py (L3255-3274)
```python
    async def dl_new_mirror(
        self,
        request: DLNewMirror,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> DLNewMirrorResponse:
        """Add a new on chain message for a specific singleton"""
        if self.service.wallet_state_manager is None:
            raise ValueError("The wallet service is not currently initialized")

        dl_wallet = await self.service.wallet_state_manager.get_dl_wallet()
        async with self.service.wallet_state_manager.lock:
            await dl_wallet.create_new_mirror(
                request.launcher_id,
                request.amount,
                Mirror.encode_urls(request.urls),
                action_scope,
                fee=request.fee,
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

**File:** chia/data_layer/download_data.py (L298-323)
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
```

**File:** .cursor/context/data-layer.md (L88-88)
```markdown
- Plugin and mirror URLs are external trust inputs. Downloaded data must continue to be verified against wallet-advertised roots, and file path/name validation must stay strict.
```
