### Title
Pool wallet accepts a zero (unspendable) `target_puzzle_hash` when joining or self-pooling, permanently misdirecting future farming rewards - (File: chia/pools/pool_wallet.py)

### Summary
`PoolWallet._verify_pool_state()` only checks that `target_puzzle_hash is not None`; it never rejects `bytes32.zeros` (or any other unclaimable puzzle hash). This value is the payout destination for the plot-NFT/pool singleton, so an owner-key holder can call the `pw_join_pool` / `create_new_pool_wallet` (self-pooling) wallet RPCs with a zero puzzle hash and have all future reward coins permanently unspendable, analogous to the referenced `transferAdminRights` bug where a privileged role/value is transferred to the zero address with no guard.

### Finding Description
`PoolState.target_puzzle_hash` is the final payout destination for a plot-NFT's rewards, distinct from the p2-singleton address that block rewards are initially farmed to: [1](#0-0) .

Validation of a target/initial pool state is centralized in `_verify_pool_state`, which only guards against `None`: [2](#0-1) 

This validator is invoked from `_verify_initial_target_state`, which is called by `join_pool()` before generating the travel transaction that commits the new `target_puzzle_hash` into the pool singleton's on-chain state: [3](#0-2) [4](#0-3) 

The same unchecked `target_puzzle_hash` is also used directly to build the escaping/self-pooling inner puzzles when creating a new pool wallet (self-pooling path), with no zero-address guard: [5](#0-4) 

Existing tests explicitly demonstrate that a zero `target_puzzlehash` is accepted by `pw_join_pool` without any error: [6](#0-5) 

Once committed on-chain, `PoolWallet.get_current_state()`/`apply_state_transition()` treat this as durable state and `update_pool_config()` mirrors it into the farmer-facing YAML config as the effective payout puzzle hash: [7](#0-6) [8](#0-7) 

Because `bytes32.zeros` does not correspond to any puzzle whose solving key is known to the wallet, any XCH sent to that puzzle hash (self-pooled reward claims, or the pool's eventual farming-to-pool payout target) becomes permanently unspendable — the coin exists on-chain but no one can ever produce a valid solution for its puzzle hash.

### Impact Explanation
This is a Medium-severity, spend-triggered fund-loss issue: a legitimate wallet owner action (`pw_join_pool` / creating a self-pooling plot-NFT) with an accidental or malformed `target_puzzle_hash` of all zeros results in permanent, irreversible loss of all future reward coins sent to that puzzle hash, with no on-chain or wallet-level safeguard preventing it. The impact is directly analogous to the original report: a critical configuration value (destination for value transfer / delegated control) is silently accepted as the zero address, causing unrecoverable loss.

### Likelihood Explanation
Likelihood is limited by the fact that this requires an explicit user/owner-authorized RPC call (`pw_join_pool` or self-pool creation) with a mistaken or externally-supplied zero puzzle hash (e.g., a bug in tooling/automation, a copy-paste/parsing error producing an all-zero value, or a malicious pool advertising a bogus `target_puzzlehash`/`new_pool_url` value that downstream automation blindly forwards). It does not require any attacker to compromise the wallet, and there is no confirmation step or on-chain safeguard once the travel transaction commits the state.

### Recommendation
Add an explicit rejection of `bytes32.zeros` (and any other clearly-unspendable placeholder puzzle hash) in `PoolWallet._verify_pool_state()`, alongside the existing `None` check, so that both `join_pool()`/`_verify_initial_target_state()` and self-pooling wallet creation reject a zero `target_puzzle_hash` before generating and broadcasting the pool-state transition spend.

### Proof of Concept
1. Call the `pw_join_pool` RPC with `target_puzzlehash=bytes32.zeros`, as already exercised (without assertion of failure) in the existing test suite: [6](#0-5) .
2. `PoolWallet.join_pool()` calls `_verify_initial_target_state(target_state)` → `_verify_pool_state()`, which passes because `target_puzzle_hash` is not `None`: [9](#0-8) .
3. `generate_travel_transactions()` builds and broadcasts the singleton travel spend committing the zero puzzle hash as the new pool state's payout target: [10](#0-9) .
4. Once the singleton transitions to `FARMING_TO_POOL`/leaves and eventually pays out, or once self-pooled rewards are absorbed and swept to `target_puzzle_hash`, the resulting coins are created at puzzle hash `0x00…00`, which no key can solve — the funds are permanently locked/burned.

### Citations

**File:** chia/pools/pool_wallet_info.py (L44-61)
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
    # owner_pubkey is set by the wallet, once
    owner_pubkey: G1Element
    pool_url: str | None
    relative_lock_height: uint32
```

**File:** chia/pools/pool_wallet.py (L164-187)
```python
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

    @classmethod
    def _verify_initial_target_state(cls, initial_target_state: PoolState) -> None:
        err = cls._verify_pool_state(initial_target_state)
        if err:
            raise ValueError(f"Invalid internal Pool State: {err}: {initial_target_state}")
```

**File:** chia/pools/pool_wallet.py (L232-260)
```python
    async def update_pool_config(self, action_scope: WalletActionScope) -> None:
        current_state: PoolWalletInfo = await self.get_current_state()
        if current_state.p2_singleton_puzzle_hash not in PoolingShareState.get_all_p2_singleton_puzzle_hashes(
            root_path=self.wallet_state_manager.root_path
        ):
            PoolingShareState(
                launcher_id=current_state.launcher_id,
                pool_url=current_state.current.pool_url if current_state.current.pool_url else "",
                payout_instructions=(await action_scope.get_puzzle_hash(self.wallet_state_manager)).hex(),
                p2_singleton_puzzle_hash=current_state.p2_singleton_puzzle_hash,
                owner_public_key=current_state.current.owner_pubkey,
                target_puzzle_hash=current_state.current.target_puzzle_hash,
                key_derivation_index=-1,
            ).add(root_path=self.wallet_state_manager.root_path)
        with PoolingShareState.acquire(
            root_path=self.wallet_state_manager.root_path,
            p2_singleton_puzzle_hash=current_state.p2_singleton_puzzle_hash,
        ) as pool_config:
            payout_instructions = pool_config.payout_instructions
            if payout_instructions == "":
                payout_instructions = (await action_scope.get_puzzle_hash(self.wallet_state_manager)).hex()
                self.log.info(f"New config entry. Generated payout_instructions puzzle hash: {payout_instructions}")

            pool_config.launcher_id = current_state.launcher_id
            pool_config.pool_url = current_state.current.pool_url if current_state.current.pool_url else ""
            pool_config.payout_instructions = payout_instructions
            pool_config.target_puzzle_hash = current_state.current.target_puzzle_hash
            pool_config.p2_singleton_puzzle_hash = current_state.p2_singleton_puzzle_hash
            pool_config.owner_public_key = current_state.current.owner_pubkey
```

**File:** chia/pools/pool_wallet.py (L262-304)
```python
    async def apply_state_transition(
        self, new_state: CoinSpend, block_height: uint32, action_scope: WalletActionScope
    ) -> bool:
        """
        Updates the Pool state (including DB) with new singleton spends.
        The DB must be committed after calling this method. All validation should be done here. Returns True iff
        the spend is a valid transition spend for the singleton, False otherwise.
        """
        tip: tuple[uint32, CoinSpend] = await self.get_tip()
        tip_spend = tip[1]

        tip_coin: Coin | None = get_most_recent_singleton_coin_from_coin_spend(tip_spend)
        assert tip_coin is not None
        spent_coin_name: bytes32 = tip_coin.name()

        if spent_coin_name != new_state.coin.name():
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            if new_state.coin.name() in [sp.coin.name() for _, sp in history]:
                self.log.info(f"Already have state transition: {new_state.coin.name().hex()}")
            else:
                self.log.warning(
                    f"Failed to apply state transition. tip: {tip_coin} new_state: {new_state} height {block_height}"
                )
            return False

        await self.wallet_state_manager.pool_store.add_spend(self.wallet_id, new_state, block_height)
        tip_spend = (await self.get_tip())[1]
        self.log.info(f"New PoolWallet singleton tip_coin: {tip_spend} farmed at height {block_height}")

        # If we have reached the target state, resets it to None. Loops back to get current state
        for _, added_spend in reversed(
            await self.wallet_state_manager.pool_store.get_spends_for_wallet(self.wallet_id)
        ):
            latest_state: PoolState | None = solution_to_pool_state(added_spend)
            if latest_state is not None:
                if self.target_state == latest_state:
                    self.target_state = None
                    self.next_transaction_fee = uint64(0)
                    self.next_tx_config = DEFAULT_TX_CONFIG
                break

        await self.update_pool_config(action_scope)
        return True
```

**File:** chia/pools/pool_wallet.py (L462-497)
```python
    async def generate_travel_transactions(self, fee: uint64, action_scope: WalletActionScope) -> None:
        # target_state is contained within pool_wallet_state
        pool_wallet_info: PoolWalletInfo = await self.get_current_state()

        spend_history = await self.get_spend_history()
        last_coin_spend: CoinSpend = spend_history[-1][1]
        delayed_seconds, delayed_puzhash = get_delayed_puz_info_from_launcher_spend(spend_history[0][1])
        assert pool_wallet_info.target is not None
        next_state = pool_wallet_info.target
        if pool_wallet_info.current.state == FARMING_TO_POOL.value:
            next_state = create_pool_state(
                LEAVING_POOL,
                pool_wallet_info.current.target_puzzle_hash,
                pool_wallet_info.current.owner_pubkey,
                pool_wallet_info.current.pool_url,
                pool_wallet_info.current.relative_lock_height,
            )

        new_inner_puzzle = pool_state_to_inner_puzzle(
            next_state,
            pool_wallet_info.launcher_coin.name(),
            self.wallet_state_manager.constants.GENESIS_CHALLENGE,
            delayed_seconds,
            delayed_puzhash,
        )
        new_full_puzzle = create_full_puzzle(new_inner_puzzle, pool_wallet_info.launcher_coin.name()).to_serialized()

        outgoing_coin_spend, inner_puzzle = create_travel_spend(
            last_coin_spend,
            pool_wallet_info.launcher_coin,
            pool_wallet_info.current,
            next_state,
            self.wallet_state_manager.constants.GENESIS_CHALLENGE,
            delayed_seconds,
            delayed_puzhash,
        )
```

**File:** chia/pools/pool_wallet.py (L569-596)
```python
        escaping_inner_puzzle: Program = create_waiting_room_inner_puzzle(
            initial_target_state.target_puzzle_hash,
            initial_target_state.relative_lock_height,
            initial_target_state.owner_pubkey,
            launcher_coin.name(),
            genesis_challenge,
            delay_time,
            delay_ph,
        )
        escaping_inner_puzzle_hash = escaping_inner_puzzle.get_tree_hash()

        self_pooling_inner_puzzle: Program = create_pooling_inner_puzzle(
            initial_target_state.target_puzzle_hash,
            escaping_inner_puzzle_hash,
            initial_target_state.owner_pubkey,
            launcher_coin.name(),
            genesis_challenge,
            delay_time,
            delay_ph,
        )

        if initial_target_state.state == SELF_POOLING.value:
            puzzle = escaping_inner_puzzle
        elif initial_target_state.state == FARMING_TO_POOL.value:
            puzzle = self_pooling_inner_puzzle
        else:
            raise ValueError("Invalid initial state")
        full_pooling_puzzle: Program = create_full_puzzle(puzzle, launcher_id=launcher_coin.name())
```

**File:** chia/pools/pool_wallet.py (L630-672)
```python
    async def join_pool(self, target_state: PoolState, fee: uint64, action_scope: WalletActionScope) -> uint64:
        if target_state.state != FARMING_TO_POOL.value:
            raise ValueError(f"join_pool must be called with target_state={FARMING_TO_POOL} (FARMING_TO_POOL)")
        if self.target_state is not None:
            raise ValueError(f"Cannot join a pool while waiting for target state: {self.target_state}")
        if await self.have_unconfirmed_transaction():
            raise ValueError(
                "Cannot join pool due to unconfirmed transaction. If this is stuck, delete the unconfirmed transaction."
            )

        current_state: PoolWalletInfo = await self.get_current_state()

        total_fee = fee
        if current_state.current == target_state:
            self.target_state = None
            msg = f"Asked to change to current state. Target = {target_state}"
            self.log.info(msg)
            raise ValueError(msg)
        elif current_state.current.state in {SELF_POOLING.value, LEAVING_POOL.value}:
            total_fee = fee
        elif current_state.current.state == FARMING_TO_POOL.value:
            total_fee = uint64(fee * 2)

        if self.target_state is not None:
            raise ValueError(
                f"Cannot change to state {target_state} when already having target state: {self.target_state}"
            )
        PoolWallet._verify_initial_target_state(target_state)
        if current_state.current.state == LEAVING_POOL.value:
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            last_height: uint32 = history[-1][0]
            if (
                await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
                <= last_height + current_state.current.relative_lock_height
            ):
                raise ValueError(
                    f"Cannot join a pool until height {last_height + current_state.current.relative_lock_height}"
                )

        self.target_state = target_state
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
```

**File:** chia/_tests/pools/test_pool_rpc.py (L1200-1212)
```python
        # Join a different pool
        await wallet_rpc.pw_join_pool(
            PWJoinPool(
                wallet_id=uint32(wallet_id),
                target_puzzlehash=bytes32.zeros,
                pool_url="https://pool-b.org",
                relative_lock_height=LOCK_HEIGHT,
                fee=uint64(fee),
                push=True,
            ),
            DEFAULT_TX_CONFIG,
        )

```
