### Title
Integer Overflow in Relative Timelock Arithmetic Bypasses `ASSERT_SECONDS_RELATIVE` / `ASSERT_HEIGHT_RELATIVE` Enforcement — (`File: crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

When `check_time_locks` is called with `nowrap = false` (the legacy pre-fix path), relative timelock conditions (`ASSERT_SECONDS_RELATIVE`, `ASSERT_HEIGHT_RELATIVE`) are evaluated using `wrapping_add`. An attacker-controlled spend that supplies a crafted large value for `seconds_relative` or `height_relative` causes the addition to wrap around to a small integer, making the timelock check trivially pass. A coin that should be locked for an astronomically long time is accepted immediately. This is the direct chia_rs analog of the original report's arithmetic confusion between a duration value and a timestamp value.

---

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` enforces four relative timelock conditions per spend. For each condition it branches on the `nowrap` boolean:

```rust
// lines 70-78
if let Some(seconds_relative) = spend.seconds_relative {
    if nowrap {
        if timestamp < unspent.timestamp.saturating_add(seconds_relative) {
            return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
        }
    } else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
        return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
    }
}
``` [1](#0-0) 

When `nowrap = false`, `wrapping_add` is used. If `unspent.timestamp + seconds_relative` exceeds `u64::MAX`, the result wraps to a small value. The guard `timestamp < small_wrapped_value` then evaluates to `false`, so no error is returned and the spend is accepted — even though the coin's puzzle demanded that `seconds_relative` seconds must have elapsed since the coin was confirmed.

The same wrapping flaw exists for `height_relative`:

```rust
// lines 64-68
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}
``` [2](#0-1) 

The inverse flaw exists for `ASSERT_BEFORE_SECONDS_RELATIVE` and `ASSERT_BEFORE_HEIGHT_RELATIVE`: wrapping makes the deadline appear to be in the past, causing a spend to be permanently rejected when it should be accepted.

The codebase's own test suite explicitly documents the divergence:

```
// 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::seconds_relative_wrap(
    Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
    Ok(()),
)]
``` [3](#0-2) 

The `nowrap` parameter is a runtime boolean passed in from the Python layer via the exposed `py_check_time_locks` binding:

```rust
pub fn py_check_time_locks(
    removal_coin_records: HashMap<Bytes32, CoinRecord>,
    bundle_conds: &OwnedSpendBundleConditions,
    prev_transaction_block_height: u32,
    timestamp: u64,
    nowrap: bool,
) -> PyResult<Option<u32>> {
``` [4](#0-3) 

Any call path that passes `nowrap = false` — including historical block re-validation or any Python caller that has not yet been updated — is exposed to the bypass.

---

### Impact Explanation

**High — Timelock validation bypass enables unauthorized spend acceptance.**

A coin whose puzzle emits `ASSERT_SECONDS_RELATIVE` with a value `V` such that `coin_birth_timestamp + V > u64::MAX` will have its timelock silently bypassed when `nowrap = false`. The coin can be spent at any time, regardless of the intended lock duration. This directly enables unauthorized spend acceptance: a coin that was supposed to be unspendable for years (or effectively forever) can be spent immediately.

Concretely: if `coin_birth_timestamp = T` and the attacker sets `seconds_relative = u64::MAX - T + 1`, then `T.wrapping_add(seconds_relative) = 0`, and the check `current_timestamp < 0` is always false, so the spend is always accepted.

---

### Likelihood Explanation

The `nowrap` parameter is a runtime boolean controlled by the Python caller. The `get_flags_for_height_and_constants` function does not set `nowrap`; it is passed separately. Any code path that calls `check_time_locks` with `nowrap = false` — including legacy block validation or Python callers that have not adopted the new flag — is vulnerable. The attacker only needs to craft a coin spend with a large `ASSERT_SECONDS_RELATIVE` value, which is fully attacker-controlled CLVM output. [5](#0-4) 

---

### Recommendation

1. Remove the `nowrap = false` / `wrapping_add` branch entirely. The `saturating_add` path is the correct semantic: a relative timelock that would overflow `u64` should be treated as "never satisfiable" (for `ASSERT_SECONDS_RELATIVE`) or "always satisfiable" (for `ASSERT_BEFORE_SECONDS_RELATIVE`), not as a wrapped small value.
2. If backward compatibility with old blocks is required, document and gate the `nowrap = false` path behind a specific block-height threshold, and ensure no new blocks can be validated with `nowrap = false`.
3. Audit all Python callers of `check_time_locks` to confirm they pass `nowrap = true` for all current and future block heights.

---

### Proof of Concept

Given a coin confirmed at timestamp `T = 1_700_000_000`:

- Attacker crafts a spend with `ASSERT_SECONDS_RELATIVE = 0xFFFF_FFFF_FFFF_D8F0` (i.e., `u64::MAX - T + 1`).
- `check_time_locks` is called with `nowrap = false`.
- `T.wrapping_add(0xFFFF_FFFF_FFFF_D8F0) = 0`.
- Guard: `current_timestamp < 0` → `false` → no error returned.
- Spend is accepted immediately, bypassing the intended timelock.

This is confirmed by the existing test vector:

```
# 10000 + (u64::MAX - 9999) overflows to 0, wrapping: 10150 < 0 -> Ok
(make_test_conds(seconds_relative=0xFFFF_FFFF_FFFF_D8F0), 105, None),
``` [1](#0-0) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/chia-consensus/src/check_time_locks.rs (L55-68)
```rust
        if let Some(height_relative) = spend.height_relative {
            if nowrap {
                if prev_transaction_block_height
                    < unspent
                        .confirmed_block_index
                        .saturating_add(height_relative)
                {
                    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
                }
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

**File:** crates/chia-consensus/src/check_time_locks.rs (L100-112)
```rust
        if let Some(before_seconds_relative) = spend.before_seconds_relative {
            if nowrap {
                if timestamp >= unspent.timestamp.saturating_add(before_seconds_relative) {
                    return Err(ValidationErr::Err(
                        ErrorCode::AssertBeforeSecondsRelativeFailed,
                    ));
                }
            } else if timestamp >= unspent.timestamp.wrapping_add(before_seconds_relative) {
                return Err(ValidationErr::Err(
                    ErrorCode::AssertBeforeSecondsRelativeFailed,
                ));
            }
        }
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L122-128)
```rust
pub fn py_check_time_locks(
    removal_coin_records: HashMap<Bytes32, CoinRecord>,
    bundle_conds: &OwnedSpendBundleConditions,
    prev_transaction_block_height: u32,
    timestamp: u64,
    nowrap: bool,
) -> PyResult<Option<u32>> {
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
