### Title
Relative Timelock Bypass via Integer Wrapping in `check_time_locks` — (File: `crates/chia-consensus/src/check_time_locks.rs`)

### Summary
When `nowrap=false`, `check_time_locks` uses `wrapping_add` to compute the deadline for `ASSERT_SECONDS_RELATIVE` and `ASSERT_HEIGHT_RELATIVE` conditions. An attacker-controlled `seconds_relative` (or `height_relative`) value can be chosen to cause the sum to wrap around to a value smaller than the current timestamp/height, making the timelock check pass immediately — a direct analog of the Solidity bug where `releaseTime_` omits `block.timestamp`.

### Finding Description

`check_time_locks` branches on the `nowrap` flag for all four relative-timelock conditions: [1](#0-0) 

When `nowrap=false`, the deadline is computed as:

```
unspent.timestamp.wrapping_add(seconds_relative)
```

If an attacker sets `seconds_relative = u64::MAX - coin_birth_timestamp + 1`, the wrapping sum equals `0`. The guard `timestamp < 0` is always false, so the spend is accepted immediately regardless of the actual current time. The same overflow applies to `height_relative` via `u32` wrapping: [2](#0-1) 

The test suite explicitly documents and confirms this behavior: [3](#0-2) [4](#0-3) 

The Python binding `py_check_time_locks` forwards the `nowrap` parameter directly from the caller without any enforcement: [5](#0-4) 

The `nowrap` flag is not derived from any consensus constant inside chia_rs itself — it is supplied entirely by the Python chia-blockchain layer at call time. For any block height where the Python node passes `nowrap=False` (legacy/backward-compatibility path), the wrapping bypass is live.

### Impact Explanation

A coin whose puzzle emits `(ASSERT_SECONDS_RELATIVE . <overflow_value>)` or `(ASSERT_HEIGHT_RELATIVE . <overflow_value>)` will pass the timelock check immediately when validated under `nowrap=False`. This constitutes a **timelock condition validation bypass** — the spend is accepted as consensus-valid before the intended lock period has elapsed. In multi-party constructs (escrow, payment channels, vesting) where the timelock is the only enforcement mechanism, this allows premature withdrawal of locked funds.

This matches the allowed High impact: *"timelock … condition … validation bypass enables unauthorized spend acceptance."*

### Likelihood Explanation

- The attacker controls `seconds_relative` / `height_relative` through the CLVM puzzle they author — a fully unprivileged, on-chain entry path.
- The `nowrap=False` code path is preserved in the production binary and is reachable whenever the Python full node passes `nowrap=False` (e.g., for pre-activation-height blocks or any caller that does not yet set `nowrap=True`).
- The exact overflow value to use is trivially computable from the coin's public `confirmed_block_index` / `timestamp` fields.

### Recommendation

1. Remove the `nowrap=false` / `wrapping_add` branch entirely from `check_time_locks` and always use `saturating_add`. Backward compatibility for historical blocks should be handled at the Python layer by re-validating with the correct semantics, not by keeping a broken code path in the library.
2. If the legacy path must be retained for chain-replay purposes, gate it behind a hard-fork height constant inside chia_rs (analogous to `hard_fork_height` / `soft_fork8_height` in `get_flags_for_height_and_constants`) so the wrapping path cannot be selected for any block above that height. [6](#0-5) 

### Proof of Concept

Given a coin confirmed at timestamp `T` (e.g., `T = 1_700_000_000` on mainnet):

1. Author a CLVM puzzle that outputs condition `(80 . 0xFFFFFFFFFFFFFFFF)` (`ASSERT_SECONDS_RELATIVE` opcode 80, value `u64::MAX`).
2. Fund the coin. Its `CoinRecord.timestamp = T`.
3. Immediately submit a spend bundle for this coin.
4. The validator calls `check_time_locks(..., nowrap=false)`.
5. Deadline = `T.wrapping_add(u64::MAX)` = `T - 1` (wraps to a value less than `T`).
6. Check: `current_timestamp < (T - 1)` → **false** → `Ok(())` → spend accepted.

The coin is spent with zero waiting time despite the nominally enormous `ASSERT_SECONDS_RELATIVE` value, directly mirroring the Solidity pattern where `releaseTime_` omits the current-time base. [7](#0-6)

### Citations

**File:** crates/chia-consensus/src/check_time_locks.rs (L64-68)
```rust
            } else if prev_transaction_block_height
                < unspent.confirmed_block_index.wrapping_add(height_relative)
            {
                return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
            }
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L70-78)
```rust
        if let Some(seconds_relative) = spend.seconds_relative {
            if nowrap {
                if timestamp < unspent.timestamp.saturating_add(seconds_relative) {
                    return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
                }
            } else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
                return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
            }
        }
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L122-141)
```rust
pub fn py_check_time_locks(
    removal_coin_records: HashMap<Bytes32, CoinRecord>,
    bundle_conds: &OwnedSpendBundleConditions,
    prev_transaction_block_height: u32,
    timestamp: u64,
    nowrap: bool,
) -> PyResult<Option<u32>> {
    let res = check_time_locks(
        &removal_coin_records,
        bundle_conds,
        prev_transaction_block_height,
        timestamp,
        nowrap,
    );

    match res {
        Ok(()) => Ok(None),
        Err(ec) => Ok(Some(ec.error_code().into())),
    }
}
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L276-281)
```rust
    // 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
    #[case::height_relative_wrap(
        Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
        Ok(()),
    )]
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L301-306)
```rust
    // 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
    #[case::seconds_relative_wrap(
        Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
        Ok(()),
    )]
```

**File:** crates/chia-consensus/src/spendbundle_validation.rs (L61-102)
```rust
pub fn get_flags_for_height_and_constants(
    prev_tx_height: u32,
    constants: &ConsensusConstants,
) -> ConsensusFlags {
    //  the hard-fork initiated with 2.0. To activate June 2024
    //  * costs are ascribed to some unknown condition codes, to allow for
    // soft-forking in new conditions with cost
    //  * a new condition, SOFTFORK, is added which takes a first parameter to
    //    specify its cost. This allows soft-forks similar to the softfork
    //    operator
    //  * BLS operators introduced in the soft-fork (behind the softfork
    //    guard) are made available outside of the guard.
    //  * division with negative numbers are allowed, and round toward
    //    negative infinity
    //  * AGG_SIG_* conditions are allowed to have unknown additional
    //    arguments
    //  * Allow the block generator to be serialized with the improved clvm
    //   serialization format (with back-references)

    // The soft fork initiated with 2.5.0. The activation date is still TBD.
    // Adds a new keccak256 operator under the softfork guard with extension 1.
    // This operator can be hard forked in later, but is not included in a hard fork yet.

    // In hard fork 2, we enable the keccak operator outside the softfork guard
    let mut flags = ConsensusFlags::empty();
    if prev_tx_height >= constants.hard_fork2_height {
        flags |= ConsensusFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD
            | ConsensusFlags::COST_CONDITIONS
            | ConsensusFlags::ENABLE_SECP_OPS
            | ConsensusFlags::RELAXED_BLS;
    }

    if prev_tx_height >= constants.soft_fork8_height {
        flags |= ConsensusFlags::DISABLE_OP;
    }

    if prev_tx_height >= constants.soft_fork9_height {
        flags |= ConsensusFlags::SIMPLE_GENERATOR
            | ConsensusFlags::CANONICAL_INTS
            | ConsensusFlags::LIMIT_SPENDS;
    }
    flags
```
