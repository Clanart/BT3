No vulnerability found for this question.

The reported issue is specific to a Solidity vote-escrow ("vePeg") contract pattern — a mutable per-token lock struct with a `perpetuallyLocked` flag and an `increase_amount()` function whose expiry check `_locked.end > block.timestamp` incorrectly excludes locks with `end == 0` (perpetual locks). Chia has no analogous on-chain construct: there is no vote-escrow/lock-NFT contract, and the closest lock-like primitives in scope are:

- The wallet clawback puzzle (`chia/wallet/puzzles/clawback/drivers.py` and `chia/wallet/puzzles/clawback/puzzle_decorator.py`), which enforces a single fixed `ASSERT_SECONDS_RELATIVE` timelock at coin creation via `create_clawback_merkle_tree()` [1](#0-0) . It has no concept of a "perpetual" vs "time-limited" lock state, and no function analogous to `increase_amount()` that adds value to an existing locked coin while preserving its lock — clawback coins are immutable UTXOs, not stateful lock records that can be topped up.
- Mempool/consensus timelock checks (`ASSERT_SECONDS_ABSOLUTE`, `ASSERT_BEFORE_SECONDS_RELATIVE`, etc. in `chia/full_node/mempool_manager.py`) which are per-spend conditions evaluated once per spend bundle, not a persistent "lock end = 0 means perpetual" data field that a follow-up function might mishandle [2](#0-1) .
- Pool/plotnft singleton state transitions (`chia/_tests/pools/test_pool_puzzles_lifecycle.py`) involve escrow-like delay times, but again no "increase amount without resetting unlock time" operation exists [3](#0-2) .

Since Chia's UTXO/coin model has no stateful, upgradable lock record with a permanent/perpetual flag and a corresponding `increase_amount()`-style entry point, there is no reachable analog to the reported bug class within scope.

### Citations

**File:** chia/wallet/puzzles/clawback/drivers.py (L70-84)
```python
def create_clawback_merkle_tree(timelock: uint64, sender_ph: bytes32, recipient_ph: bytes32) -> MerkleTree:
    """
    Returns a merkle tree object
    For clawbacks there are only 2 puzzles in the merkle tree, claim puzzle and clawback puzzle
    """
    if timelock < 1:
        raise ValueError("Timelock must be at least 1 second")
    timelock_condition = [ConditionOpcode.ASSERT_SECONDS_RELATIVE, timelock]
    augmented_cond_puz_hash = create_augmented_cond_puzzle_hash(timelock_condition, recipient_ph)
    merkle_tree = MerkleTree(
        [
            augmented_cond_puz_hash,
            curry_and_treehash(P2_CURRIED_PUZZLE_MOD_HASH_QUOTED, Program.to(sender_ph).get_tree_hash()),
        ]
    )
```

**File:** chia/full_node/mempool_manager.py (L82-127)
```python
def compute_assert_height(
    removal_coin_records: dict[bytes32, CoinRecord],
    conds: SpendBundleConditions,
) -> TimelockConditions:
    """
    Computes the most restrictive height- and seconds assertion in the spend bundle.
    Relative heights and times are resolved using the confirmed heights and
    timestamps from the coin records.
    """

    ret = TimelockConditions()
    ret.assert_height = uint32(conds.height_absolute)
    ret.assert_seconds = uint64(conds.seconds_absolute)
    ret.assert_before_height = (
        uint32(conds.before_height_absolute) if conds.before_height_absolute is not None else None
    )
    ret.assert_before_seconds = (
        uint64(conds.before_seconds_absolute) if conds.before_seconds_absolute is not None else None
    )

    for spend in conds.spends:
        if spend.height_relative is not None:
            h = uint32(removal_coin_records[bytes32(spend.coin_id)].confirmed_block_index + spend.height_relative)
            ret.assert_height = max(ret.assert_height, h)

        if spend.seconds_relative is not None:
            s = uint64(removal_coin_records[bytes32(spend.coin_id)].timestamp + spend.seconds_relative)
            ret.assert_seconds = max(ret.assert_seconds, s)

        if spend.before_height_relative is not None:
            h = uint32(
                removal_coin_records[bytes32(spend.coin_id)].confirmed_block_index + spend.before_height_relative
            )
            if ret.assert_before_height is not None:
                ret.assert_before_height = min(ret.assert_before_height, h)
            else:
                ret.assert_before_height = h

        if spend.before_seconds_relative is not None:
            s = uint64(removal_coin_records[bytes32(spend.coin_id)].timestamp + spend.before_seconds_relative)
            if ret.assert_before_seconds is not None:
                ret.assert_before_seconds = min(ret.assert_before_seconds, s)
            else:
                ret.assert_before_seconds = s

    return ret
```

**File:** chia/_tests/pools/test_pool_puzzles_lifecycle.py (L295-320)
```python
        # Test that we can retrieve the extra data
        assert solution_to_pool_state(travel_coinsol) == target_pool_state
        # sign the serialized state
        data = Program.to(bytes(target_pool_state)).get_tree_hash()
        sig: G2Element = AugSchemeMPL.sign(
            sk,
            (data + singleton.name() + DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA),
        )
        # Spend it!
        coin_db.update_coin_store_for_spend_bundle(
            SpendBundle([travel_coinsol], sig), time, DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM
        )

        # ESCAPE TOO FAST (Negative test)
        # find the singleton
        singleton = get_most_recent_singleton_coin_from_coin_spend(travel_coinsol)
        # get the relevant coin solution
        return_coinsol, _ = create_travel_spend(
            travel_coinsol,
            launcher_coin,
            target_pool_state,
            pool_state,
            GENESIS_CHALLENGE,
            DELAY_TIME,
            DELAY_PH,
        )
```
