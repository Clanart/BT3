### Title
Missing zero/burn-address validation on pool `target_puzzle_hash` in `PoolWallet` state verification can cause permanent loss of farmed rewards - (File: chia/pools/pool_wallet.py)

### Summary
`PoolWallet._verify_self_pooled()` and `PoolWallet._verify_pooling_state()`, called via `_verify_pool_state()`/`_verify_initial_target_state()`, validate `pool_url` and `relative_lock_height` fields but never validate that `state.target_puzzle_hash` is not the all-zero/burn puzzle hash (`bytes32.zeros`). This mirrors the reported `LMPVault.setRewarder()` pattern: a state-mutating setter checks some invariants (e.g. "already set") but omits a check for a degenerate/zero destination address, so funds routed to it become permanently unspendable.

### Finding Description
`_verify_pool_state()` only rejects a `None` `target_puzzle_hash`: [1](#0-0) 

`_verify_self_pooled()` and `_verify_pooling_state()` never inspect `target_puzzle_hash` at all: [2](#0-1) 

This `target_puzzle_hash` is the "final payout destination" for pool/self-farmed rewards, as documented in `pool_wallet_info.py`: [3](#0-2) 

The value is user/RPC supplied when joining a pool (`PWJoinPool.target_puzzlehash`) and is used unchecked to construct the on-chain pool inner puzzle (`create_pooling_inner_puzzle` / `pool_state_to_inner_puzzle` → `create_travel_spend`), which becomes the coin puzzle hash that block/pool rewards are ultimately swept to: [4](#0-3) 

The test suite itself demonstrates that `bytes32.zeros` is accepted as a valid `target_puzzlehash` for `pw_join_pool` without any rejection, confirming no zero-address guard exists on this path: [5](#0-4) [6](#0-5) 

### Impact Explanation
If `target_puzzle_hash` is ever set to `bytes32.zeros` (or any other puzzle hash with no known private key) — whether through a client bug, malformed pool response consumed by `join_pool()` in `plotnft_funcs.py`, or manual RPC misuse — every subsequent self-pooling/farming-to-pool reward absorbed through `create_absorb_spend` will be paid to that unspendable puzzle hash. Because Chia coin ownership is permanently determined by puzzle hash, and there is no recovery mechanism for a puzzle hash with no corresponding key, this results in permanent, irrecoverable loss of the farmed block rewards for that plot-NFT/singleton. This matches a Medium-severity "no zero-address check causing irreversible loss of funds" class, analogous to the reported `LMPVault::setRewarder()` issue.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a user, wallet client, or pool-server response to supply a degenerate `target_puzzle_hash` (e.g. `bytes32.zeros` or a hash with no known key) either directly via `pw_join_pool`/`PWJoinPool.target_puzzlehash` or via `initial_target_state.target_puzzle_hash` when creating the pool wallet. Since `pool_url = join_pool()` in `chia/cmds/plotnft_funcs.py` blindly trusts `json_dict["target_puzzle_hash"]` returned by an external pool server without validating it is non-zero/well-formed, a misbehaving or buggy pool response is a realistic trigger, matching the "Very low likelihood but impact is high" rationale used for the original Medium-rated finding.

### Recommendation
Add an explicit non-zero (and ideally non-degenerate) check on `target_puzzle_hash` in `PoolWallet._verify_pool_state()` (covering both self-pooling and farming-to-pool branches) before it is accepted into `PoolState`, e.g.:
```python
if state.target_puzzle_hash == bytes32.zeros:
    return "target_puzzle_hash cannot be the zero/burn address"
```
This should be enforced both in `_verify_initial_target_state()` (pool wallet creation) and wherever a new target state is constructed for `join_pool`/`self_pool` transitions, so a zero or otherwise unspendable destination can never be committed on-chain.

### Proof of Concept
1. Call the wallet RPC `pw_join_pool` (`PWJoinPool`) with `target_puzzlehash=bytes32.zeros` (exactly as done in `chia/_tests/pools/test_pool_rpc.py:949-1014` for `test_self_pooling_to_pooling`) — the request succeeds because no validation rejects the zero puzzle hash.
2. `PoolWallet.generate_travel_transactions()` constructs the new pool inner puzzle via `pool_state_to_inner_puzzle(next_state, ...)` using this zero `target_puzzle_hash`, and the resulting singleton spend is pushed on-chain.
3. Subsequent reward absorption (`PoolWallet.claim_pool_rewards` / `create_absorb_spend`) pays out to the singleton's target puzzle hash — now the zero/burn address — with no signing key ever able to spend the resulting coin, permanently locking those rewards.

### Citations

**File:** chia/pools/pool_wallet.py (L136-162)
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
```

**File:** chia/pools/pool_wallet.py (L164-182)
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

**File:** chia/_tests/pools/test_pool_rpc.py (L949-949)
```python
        pool_ph = bytes32.zeros
```

**File:** chia/_tests/pools/test_pool_rpc.py (L1004-1014)
```python
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
```
