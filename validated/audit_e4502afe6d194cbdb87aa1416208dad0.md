### Title
No Validation of `target_puzzle_hash` (Zero/Burn Address) in `PoolWallet` State Verification - (File: `chia/pools/pool_wallet.py`)

### Summary
`PoolWallet._verify_pool_state()` and its helpers `_verify_self_pooled()` / `_verify_pooling_state()` are the only gate applied to a pool singleton's `target_puzzle_hash` before it is committed as the wallet's pending/target pool state (via `join_pool()`, `self_pool()`, and `create_new_pool_wallet_transaction()`). The check only rejects `target_puzzle_hash is None`; it never rejects the zero address (`bytes32.zeros`) or any other non-recoverable/burn puzzle hash. Since `target_puzzle_hash` is "the final payout destination" for all future farmed/absorbed pool rewards, an unvalidated value directs real XCH reward coins to an address nobody can spend from.

### Finding Description
`PoolState.target_puzzle_hash` is documented as the final destination for pool/self-pool rewards, not the transient p2-singleton address: [1](#0-0) 

The only validation performed before this value is accepted as the wallet's current or target pool state is `_verify_pool_state`, which merely checks for `None`: [2](#0-1) 

`_verify_self_pooled` and `_verify_pooling_state` validate `pool_url` and `relative_lock_height`, but neither one inspects `target_puzzle_hash` for being the zero address, a known burn address, or otherwise unspendable: [3](#0-2) 

This verification is invoked from `_verify_initial_target_state`, which is called both at pool-wallet creation time and at every `join_pool`/`self_pool` transition: [4](#0-3) [5](#0-4) 

`join_pool`/`self_pool` accept `target_puzzlehash` directly from the wallet RPC caller (`PWJoinPool.target_puzzlehash`), and in `self_pool()` the target puzzle hash for a `SELF_POOLING` transition is derived from local action-scope state, but for `join_pool` the pool-supplied/CLI-supplied `target_puzzle_hash` flows straight into `create_pool_state()` with no address-safety check: [6](#0-5) 

The test suite itself demonstrates that the zero address is accepted as a valid `target_puzzlehash` for a `pw_join_pool` transition with no error raised: [7](#0-6) 

The CLI-side `create_pool_args()` (used when joining a public pool) fetches `target_puzzle_hash` straight from the remote pool's `/pool_info` JSON response and only validates `relative_lock_height` and `protocol_version` — never the puzzle hash itself: [8](#0-7) 

### Impact Explanation
If `target_puzzle_hash` ends up set to the zero address (or any other unspendable/burn puzzle hash) — whether via a malicious/misconfigured pool server response, a mistaken CLI/RPC call, or manual JSON-RPC invocation of `pw_join_pool` — every subsequent `claim_pool_rewards()` / absorb spend for that singleton pays the pool block reward to that unspendable puzzle hash. This is concrete, spend-triggered, irreversible loss of farmed XCH funds for the plot-NFT owner, matching the "unsigned/unauthorized coin movement causing fund loss" class of impact. Because `target_puzzle_hash` also becomes `PoolState.target_puzzle_hash` committed on-chain via the singleton travel spend, the loss is durable and cannot be corrected without abandoning the singleton lineage.

### Likelihood Explanation
This is reachable purely through the standard wallet-user pool-join workflow — no privileged, malicious-peer, or malicious-farmer position is required. A user calling `pw_join_pool` with a bad/burn address (or a pool server returning a bad/attacker-controlled `target_puzzle_hash` in its `/pool_info`, which is only sanity-checked for `relative_lock_height` and `protocol_version`) directly triggers this. `_verify_initial_target_state` is invoked on every join_pool/self_pool/create transition, so there is no secondary layer of protection anywhere in the pool wallet code path.

### Recommendation
In `PoolWallet._verify_pool_state` (and/or `_verify_self_pooled`/`_verify_pooling_state`), reject `target_puzzle_hash == bytes32.zeros` (and consider rejecting other well-known burn addresses) before allowing the state to be used as `initial_target_state` or `target_state`. Apply the same check in `create_pool_args()`/CLI flows that ingest a pool-supplied `target_puzzle_hash`, so a malicious or misbehaving pool cannot silently redirect farmer rewards to an unspendable address.

### Proof of Concept
1. Create a self-pooling plot NFT, then call the wallet RPC `pw_join_pool` with `target_puzzlehash = bytes32.zeros`, a valid `pool_url`, and `relative_lock_height`, as exercised in [9](#0-8) .
2. `_verify_pool_state`/`_verify_pooling_state` accept the state because neither checks `target_puzzle_hash` for being the zero address [10](#0-9) .
3. The wallet submits the travel transaction, committing `target_puzzle_hash = 0x00..00` on-chain as the singleton's final payout puzzle hash.
4. Every future `claim_pool_rewards()`/absorb spend pays the pool reward to puzzle hash `0x00..00`, which is unspendable — resulting in permanent loss of the farmed rewards.

### Citations

**File:** chia/pools/pool_wallet_info.py (L44-57)
```python
    """
    `PoolState` is a type that is serialized to the blockchain to track the state of the user's pool singleton
    `target_puzzle_hash` is either the pool address, or the self-pooling address that pool rewards will be paid to.
    `target_puzzle_hash` is NOT the p2_singleton puzzle that block rewards are sent to.
    The `p2_singleton` address is the initial address, and the `target_puzzle_hash` is the final destination.
    `relative_lock_height` is zero when in SELF_POOLING state
    """

    version: uint8
    state: uint8  # PoolSingletonState
    # `target_puzzle_hash`: A puzzle_hash we pay to
    # When self-farming, this is a main wallet address
    # When farming-to-pool, the pool sends this to the farmer during pool protocol setup
    target_puzzle_hash: bytes32  # TODO: rename target_puzzle_hash -> pay_to_address
```

**File:** chia/pools/pool_wallet.py (L136-181)
```python
    def _verify_self_pooled(cls, state: PoolState) -> str | None:
        err = ""
        if state.pool_url not in {None, ""}:
            err += " Unneeded pool_url for self-pooling"

        if state.relative_lock_height != 0:
            err += " Incorrect relative_lock_height for self-pooling"

        return None if err == "" else err

    @classmethod
    def _verify_pooling_state(cls, state: PoolState) -> str | None:
        err = ""
        if state.relative_lock_height < cls.MINIMUM_RELATIVE_LOCK_HEIGHT:
            err += (
                f" Pool relative_lock_height ({state.relative_lock_height})"
                f"is less than recommended minimum ({cls.MINIMUM_RELATIVE_LOCK_HEIGHT})"
            )
        elif state.relative_lock_height > cls.MAXIMUM_RELATIVE_LOCK_HEIGHT:
            err += (
                f" Pool relative_lock_height ({state.relative_lock_height})"
                f"is greater than recommended maximum ({cls.MAXIMUM_RELATIVE_LOCK_HEIGHT})"
            )

        if state.pool_url in {None, ""}:
            err += " Empty pool url in pooling state"
        return err

    @classmethod
    def _verify_pool_state(cls, state: PoolState) -> str | None:
        if state.target_puzzle_hash is None:
            return "Invalid puzzle_hash"

        if state.version > POOL_PROTOCOL_VERSION:
            return (
                f"Detected pool protocol version {state.version}, which is "
                f"newer than this wallet's version ({POOL_PROTOCOL_VERSION}). Please upgrade "
                f"to use this pooling wallet"
            )

        if state.state == PoolSingletonState.SELF_POOLING.value:
            return cls._verify_self_pooled(state)
        elif state.state in {PoolSingletonState.FARMING_TO_POOL.value, PoolSingletonState.LEAVING_POOL.value}:
            return cls._verify_pooling_state(state)
        else:
            return "Internal Error"
```

**File:** chia/pools/pool_wallet.py (L183-187)
```python
    @classmethod
    def _verify_initial_target_state(cls, initial_target_state: PoolState) -> None:
        err = cls._verify_pool_state(initial_target_state)
        if err:
            raise ValueError(f"Invalid internal Pool State: {err}: {initial_target_state}")
```

**File:** chia/pools/pool_wallet.py (L425-426)
```python
        # Verify Parameters - raise if invalid
        PoolWallet._verify_initial_target_state(initial_target_state)
```

**File:** chia/pools/pool_wallet.py (L675-711)
```python
    async def self_pool(self, fee: uint64, action_scope: WalletActionScope) -> uint64:
        if await self.have_unconfirmed_transaction():
            raise ValueError(
                "Cannot self pool due to unconfirmed transaction. If this is stuck, delete the unconfirmed transaction."
            )
        pool_wallet_info: PoolWalletInfo = await self.get_current_state()
        if pool_wallet_info.current.state == SELF_POOLING.value:
            raise ValueError("Attempted to self pool when already self pooling")

        if self.target_state is not None:
            raise ValueError(f"Cannot self pool when already having target state: {self.target_state}")

        # Note the implications of getting owner_puzzlehash from our local wallet right now
        # vs. having pre-arranged the target self-pooling address
        owner_puzzlehash = await action_scope.get_puzzle_hash(self.wallet_state_manager)
        owner_pubkey = pool_wallet_info.current.owner_pubkey
        current_state: PoolWalletInfo = await self.get_current_state()
        total_fee = uint64(fee * 2)

        if current_state.current.state == LEAVING_POOL.value:
            total_fee = fee
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            last_height: uint32 = history[-1][0]
            if (
                await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
                <= last_height + current_state.current.relative_lock_height
            ):
                raise ValueError(
                    f"Cannot self pool until height {last_height + current_state.current.relative_lock_height}"
                )
        self.target_state = create_pool_state(
            SELF_POOLING, owner_puzzlehash, owner_pubkey, pool_url=None, relative_lock_height=uint32(0)
        )
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
        return total_fee
```

**File:** chia/_tests/pools/test_pool_rpc.py (L1003-1030)
```python
        await full_node_api.wait_for_wallet_synced(wallet_node=wallet_node, timeout=20)
        join_pool = await client.pw_join_pool(
            PWJoinPool(
                wallet_id=uint32(wallet_id),
                target_puzzlehash=pool_ph,
                pool_url="https://pool.example.com",
                relative_lock_height=uint32(10),
                fee=uint64(fee),
                push=True,
            ),
            DEFAULT_TX_CONFIG,
        )
        join_pool_tx: TransactionRecord = join_pool.transaction
        assert join_pool_tx is not None
        await full_node_api.wait_transaction_records_entered_mempool(records=[join_pool_tx])

        join_pool_2 = await client.pw_join_pool(
            PWJoinPool(
                wallet_id=uint32(wallet_id_2),
                target_puzzlehash=pool_ph,
                pool_url="https://pool.example.com",
                relative_lock_height=uint32(10),
                fee=uint64(fee),
                push=True,
            ),
            DEFAULT_TX_CONFIG,
        )
        join_pool_tx_2: TransactionRecord = join_pool_2.transaction
```

**File:** chia/cmds/plotnft_funcs.py (L59-79)
```python
async def create_pool_args(pool_url: str) -> dict[str, Any]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{pool_url}/pool_info", ssl=ssl_context_for_root(get_mozilla_ca_crt())) as response:
                if response.ok:
                    json_dict: dict[str, Any] = json.loads(await response.text())
                else:
                    raise ValueError(f"Response from {pool_url} not OK: {response.status}")
    except Exception as e:
        raise ValueError(f"Error connecting to pool {pool_url}: {e}")

    if json_dict["relative_lock_height"] > 1000:
        raise ValueError("Relative lock height too high for this pool, cannot join")
    if json_dict["protocol_version"] not in {1, 2}:
        raise ValueError(f"Incorrect version: {json_dict['protocol_version']}, should be 1 or 2")

    header_msg = f"\n---- Pool parameters fetched from {pool_url} ----"
    print(header_msg)
    pprint(json_dict)
    print("-" * len(header_msg))
    return json_dict
```
