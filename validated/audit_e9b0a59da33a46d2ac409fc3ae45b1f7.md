### Title
Unbounded Notification Storage Enables Wallet-Reachable Storage-Exhaustion DoS - ([File: chia/wallet/notification_manager.py])

### Summary
Any unprivileged party who can send a normal XCH transaction can flood a victim wallet's `NotificationStore` with an unlimited number of persisted "on-chain notification" records, each up to 10 KB, with no cap on total count, no per-sender rate limit, and no minimum cost floor other than a locally-configurable amount threshold. This mirrors the Discourse draft-key exhaustion bug class: cheap, unbounded, attacker-controlled resource creation on a victim-controlled store.

### Finding Description
`NotificationManager.potentially_add_new_notification` accepts any spent coin whose puzzle hash matches `construct_notification(target, amount)` and whose memoed message is `<= 10000` bytes, then unconditionally persists it via `NotificationStore.add_notification`: [1](#0-0) 

The only gating conditions are (1) `enable_notifications` config flag, (2) `coin.amount >= required_notification_amount` (a value the *recipient* configures locally, defaulting to `10000000` mojos = 0.00001 XCH), and (3) message length `<= 10000` bytes: [2](#0-1) 

There is no limit anywhere on the *number* of distinct notifications that can be stored for a given wallet, and no per-sender throttling. Each notification is keyed by a unique coin ID, so an attacker can trivially produce as many unique notification coins as they want (each new spend bundle uses a fresh coin/notification puzzle instance): [3](#0-2) 

`NotificationStore.add_notification` performs an unconditional `INSERT OR REPLACE` with no row-count cap: [4](#0-3) 

And `get_notifications` (used by the RPC `get_notifications`/`GetNotificationsCMD`) does an `ORDER BY amount DESC` scan over the entire, unbounded `notifications` table when no filter/pagination is supplied: [5](#0-4) [6](#0-5) 

The default `required_notification_amount` of `10000000` mojos (0.00001 XCH) is configured in `initial-config.yaml`, confirming the intended cost floor is dust-level, not DoS-resistant: [7](#0-6) 

An attacker (any unprivileged wallet user able to submit standard spend bundles) can repeatedly call the notification flow — i.e., create a notification coin and spend it in the same bundle, as `send_new_notification` does — against a single victim puzzle hash, each time paying only the dust `amount` (burned, since the notification coin is spent with a `NIL` solution and no `CREATE_COIN` back to the attacker) plus a minimal network fee: [8](#0-7) 

Because the message can carry up to 10 KB and there is no cap on notification count, the attacker can grow the victim's local `notifications` SQLite table without bound (e.g., thousands of 10 KB records for a fraction of an XCH in cumulative burned amount + fees), degrading wallet DB size/performance and RPC query latency (`ORDER BY` full scans) — directly analogous to Discourse's unlimited draft-key creation exhausting server resources.

### Impact Explanation
This is a resource-exhaustion / availability issue against any wallet that has `enable_notifications` on (the default) and a low `required_notification_amount` (also the shipped default). A remote, unprivileged attacker who merely knows or can compute a victim's receive puzzle hash can force unbounded persistent storage growth and query slowdown in the victim's wallet database, without needing any special privileges, cooperation, or high on-chain cost. This falls in the Medium impact band consistent with the source advisory (resource exhaustion / DoS via unbounded record creation), reachable purely from a spend-bundle submitter / wallet-user action.

### Likelihood Explanation
High likelihood of feasibility: the notification mechanism is a documented, user-facing feature (`SendNotificationCMD`/`send_notification` RPC) with a known target puzzle-hash scheme (`construct_notification`), a dust-level minimum amount, and no per-sender or aggregate rate limiting/count cap enforced anywhere in `NotificationManager` or `NotificationStore`. Any attacker capable of broadcasting ordinary spend bundles can automate this against arbitrary target puzzle hashes.

### Recommendation
- Enforce a maximum number of stored notifications per wallet (e.g., cap `notifications` table size, evicting oldest/lowest-value entries similarly to mempool eviction), rather than relying only on a per-message 10 KB cap.
- Consider requiring a materially higher minimum `required_notification_amount` by default, and/or per-sender/per-time-window rate limiting independent of amount.
- Add pagination-safe defaults to `get_notifications`/RPC calls and an index-friendly cap so a flooded table cannot cause unbounded full-table scans.

### Proof of Concept
1. Attacker learns victim's default receive puzzle hash (or any puzzle hash they own).
2. Attacker repeatedly (e.g., thousands of times) invokes the notification-send flow (`send_notification` RPC / `SendNotificationCMD`) targeting the victim, each time with `amount = required_notification_amount default (10_000_000 mojos)` and `message` close to the 10 KB limit, using a fresh coin each time.
3. Each resulting spend bundle creates a notification coin curried with `(target, amount)` and, within the same bundle, spends it with a `NIL` solution — satisfying `potentially_add_new_notification`'s checks (`chia/wallet/notification_manager.py:49-73`).
4. The victim's `NotificationStore.add_notification` (`chia/wallet/notification_store.py:67-85`) persists every one of these with no cap, growing the wallet DB by ~10 KB per record with no limit, and subsequent `get_notifications` calls (`chia/wallet/notification_store.py:87-123`) scan the ever-growing table.

### Citations

**File:** chia/wallet/notification_manager.py (L45-84)
```python
    async def potentially_add_new_notification(
        self, coin_state: CoinState, parent_spend: CoinSpend, sync_scope: WalletSyncScope
    ) -> bool:
        coin_name: bytes32 = coin_state.coin.name()
        if (
            coin_state.spent_height is None
            or not self.wallet_state_manager.wallet_node.config.get("enable_notifications", True)
            or self.wallet_state_manager.wallet_node.config.get("required_notification_amount", 100000000)
            > coin_state.coin.amount
            or await self.notification_store.notification_exists(coin_name)
        ):
            return False
        else:
            memos: dict[bytes32, list[bytes]] = compute_memos_for_spend(parent_spend)
            coin_memos: list[bytes] = memos.get(coin_name, [])
            if len(coin_memos) == 0 or len(coin_memos[0]) != 32:
                return False
            wallet_identifier = await self.wallet_state_manager.get_wallet_identifier_for_puzzle_hash(
                bytes32(coin_memos[0])
            )
            if (
                wallet_identifier is not None
                and wallet_identifier.type == WalletType.STANDARD_WALLET
                and len(coin_memos) == 2
                and construct_notification(bytes32(coin_memos[0]), uint64(coin_state.coin.amount)).get_tree_hash()
                == coin_state.coin.puzzle_hash
            ):
                if len(coin_memos[1]) > 10000:  # 10KB
                    return False
                await self.notification_store.add_notification(
                    Notification(
                        coin_state.coin.name(),
                        coin_memos[1],
                        uint64(coin_state.coin.amount),
                        uint32(coin_state.spent_height),
                    )
                )
                async with sync_scope.use() as interface:
                    interface.side_effects.websocket_events.append(WebSocketEvent(name="new_on_chain_notification"))
            return True
```

**File:** chia/wallet/notification_manager.py (L86-120)
```python
    async def send_new_notification(
        self,
        target: bytes32,
        msg: bytes,
        amount: uint64,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        coins: set[Coin] = await self.wallet_state_manager.main_wallet.select_coins(uint64(amount + fee), action_scope)
        origin_coin: bytes32 = next(iter(coins)).name()
        notification_puzzle: Program = construct_notification(target, amount)
        notification_hash: bytes32 = notification_puzzle.get_tree_hash()
        notification_coin: Coin = Coin(origin_coin, notification_hash, amount)
        notification_spend = make_spend(
            notification_coin,
            notification_puzzle,
            Program.NIL,
        )
        extra_spend_bundle = WalletSpendBundle([notification_spend], G2Element())
        await self.wallet_state_manager.main_wallet.generate_signed_transaction(
            [amount],
            [notification_hash],
            action_scope,
            fee,
            coins=coins,
            origin_id=origin_coin,
            memos=[[target, msg]],
            extra_conditions=(
                *extra_conditions,
                AssertCoinAnnouncement(asserted_id=notification_coin.name(), asserted_msg=b""),
            ),
        )
        async with action_scope.use() as interface:
            interface.side_effects.extra_spends.append(extra_spend_bundle)
```

**File:** chia/wallet/util/notifications.py (L12-13)
```python
def construct_notification(target: bytes32, amount: uint64) -> Program:
    return NOTIFICATION_MOD.curry(target, amount)
```

**File:** chia/wallet/notification_store.py (L67-85)
```python
    async def add_notification(self, notification: Notification) -> None:
        """
        Store Notification into DB
        """
        async with self.db_wrapper.writer_maybe_transaction() as conn:
            cursor = await conn.execute(
                "INSERT OR REPLACE INTO notifications (coin_id, msg, amount, height) VALUES(?, ?, ?, ?)",
                (
                    notification.id,
                    notification.message,
                    notification.amount.stream_to_bytes(),
                    notification.height,
                ),
            )
            cursor = await conn.execute(
                "INSERT OR REPLACE INTO all_notification_ids (coin_id) VALUES(?)",
                (notification.id,),
            )
            await cursor.close()
```

**File:** chia/wallet/notification_store.py (L87-123)
```python
    async def get_notifications(
        self,
        *,
        coin_ids: list[bytes32] | None = None,
        pagination: tuple[int | None, int | None] = (None, None),
    ) -> list[Notification]:
        if coin_ids is not None:
            coin_ids_str_list = "("
            for _ in coin_ids:
                coin_ids_str_list += "?"
                coin_ids_str_list += ","
            coin_ids_str_list = coin_ids_str_list[:-1] if len(coin_ids_str_list) > 1 else "("
            coin_ids_str_list += ")"
            coin_id_filter = f"WHERE coin_id IN {coin_ids_str_list} "
            coin_id_params = coin_ids
        else:
            coin_id_filter = ""
            coin_id_params = list()

        if pagination[1] is not None and pagination[0] is not None:
            pagination_str = " LIMIT ?, ?"
            pagination_params: tuple[int, ...] = (pagination[0], pagination[1] - pagination[0])
        elif pagination[1] is None and pagination[0] is not None:
            pagination_str = " LIMIT ?, (SELECT COUNT(*) from notifications)"
            pagination_params = (pagination[0],)
        elif pagination[1] is not None and pagination[0] is None:
            pagination_str = " LIMIT ?"
            pagination_params = (pagination[1],)
        else:
            pagination_str = ""
            pagination_params = tuple()

        async with self.db_wrapper.reader_no_transaction() as conn:
            rows = await conn.execute_fetchall(
                f"SELECT * from notifications {coin_id_filter}ORDER BY amount DESC{pagination_str}",
                (*coin_id_params, *pagination_params),
            )
```

**File:** chia/wallet/wallet_rpc_api.py (L1719-1726)
```python
    async def get_notifications(self, request: GetNotifications) -> GetNotificationsResponse:
        return GetNotificationsResponse(
            notifications=(
                await self.service.wallet_state_manager.notification_manager.notification_store.get_notifications(
                    coin_ids=request.ids, pagination=(request.start, request.end)
                )
            )
        )
```

**File:** chia/util/initial-config.yaml (L611-614)
```yaml
  # Enable notifications from parties on chain
  enable_notifications: True
  # The amount someone has to pay you in mojos for you to see their notification
  required_notification_amount: 10000000
```
