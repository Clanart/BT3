### Title
Integer Wrapping in Relative Timelock Arithmetic Enables Timelock Bypass — (`File: crates/chia-consensus/src/check_time_locks.rs`)

### Summary

When `check_time_locks` is invoked with `nowrap=false` (the legacy pre-soft-fork path), the relative timelock checks for `ASSERT_HEIGHT_RELATIVE` and `ASSERT_SECONDS_RELATIVE` use `wrapping_add` instead of `saturating_add`. An unprivileged spender can craft a CLVM puzzle that emits a near-maximum `height_relative` or `seconds_relative` value, causing the addition to wrap around to a small integer and making the timelock check trivially pass — bypassing the intended spend delay entirely.

### Finding Description

`check_time_locks` accepts a `nowrap: bool` parameter that selects between two arithmetic modes for relative timelock evaluation: [1](#0-0) 

When `nowrap=true`, `saturating_add` is used: overflow clamps to `u32::MAX`, so the check `prev_height < u32::MAX` correctly rejects the spend. When `nowrap=false`, `wrapping_add` is used: overflow wraps to a small value (potentially 0), so the check `prev_height < 0` is false and the spend is **accepted immediately**.

The same flaw applies to `seconds_relative` with `u64` arithmetic: [2](#0-1) 

The condition parser in `conditions.rs` accepts any value up to `u32::MAX` for `ASSERT_HEIGHT_RELATIVE` (4-byte `sanitize_uint`) and up to `u64::MAX` for `ASSERT_SECONDS_RELATIVE` (8-byte `sanitize_uint`) — both are `SanitizedUint::Ok(r)` paths, not rejected: [3](#0-2) [4](#0-3) 

The wrapping behavior is explicitly documented and tested in the codebase: [5](#0-4) [6](#0-5) 

The Python binding exposes `nowrap` as a caller-controlled parameter with no default enforcement: [7](#0-6) 

### Impact Explanation

A coin protected by `ASSERT_HEIGHT_RELATIVE N` (e.g., a time-locked vault requiring N blocks before spending) can be spent **immediately** by an attacker who crafts a puzzle emitting `height_relative = u32::MAX - confirmed_block_index + 1`. The wrapping sum equals 0, so `prev_height < 0` is false and the timelock is silently bypassed. The same applies to `ASSERT_SECONDS_RELATIVE` with a near-`u64::MAX` value. This constitutes an unauthorized spend acceptance — a High-severity timelock validation bypass.

This matches: **"Timelock or coin-id validation bypass enables unauthorized spend acceptance or replay."**

### Likelihood Explanation

- `confirmed_block_index` of any coin is publicly readable from the chain state.
- The attacker computes the exact wrapping value: `height_relative = (u32::MAX - confirmed_block_index).wrapping_add(1)`.
- The condition parser accepts this value without error (it is within the 4-byte `sanitize_uint` range).
- The `nowrap=false` path is the legacy behavior active for blocks below the soft-fork activation height. If the soft fork has not yet activated on the live network, all new mempool and block validation uses `nowrap=false`, making this immediately exploitable. Even post-activation, nodes that have not upgraded still use `nowrap=false`, creating a consensus split risk.

### Recommendation

1. Remove the `nowrap=false` / `wrapping_add` branch from `check_time_locks`. The `saturating_add` semantics are the only correct ones for timelock enforcement; wrapping arithmetic has no valid consensus use case.
2. If backward compatibility with pre-fork blocks is required, validate that `nowrap=false` is **never** used for mempool acceptance or new block validation — only for historical re-validation of already-committed blocks.
3. Add a hard assertion or type-level guarantee that `nowrap=true` is always passed in the consensus-critical call path.

### Proof of Concept

**Setup:**
- Coin confirmed at block height `10` (`confirmed_block_index = 10`).
- Current block height `prev_transaction_block_height = 15`.
- Attacker crafts a CLVM puzzle emitting: `(ASSERT_HEIGHT_RELATIVE . 0xFFFFFFF6)` (= `u32::MAX - 9`).

**Condition parsing** (`conditions.rs`, `ASSERT_HEIGHT_RELATIVE` branch):
- `sanitize_uint(..., 4, ...)` returns `SanitizedUint::Ok(0xFFFFFFF6)` — accepted.
- `height_relative = Some(0xFFFFFFF6u32)` stored in `SpendConditions`.

**Timelock check** (`check_time_locks`, `nowrap=false`):
```
confirmed_block_index.wrapping_add(height_relative)
= 10u32.wrapping_add(0xFFFFFFF6u32)
= 0x00000000  (wraps to 0)

prev_height < 0  →  15 < 0  →  false  →  Ok(())  ← timelock bypassed
```

**With `nowrap=true` (correct behavior):**
```
confirmed_block_index.saturating_add(height_relative)
= 10u32.saturating_add(0xFFFFFFF6u32)
= u32::MAX

prev_height < u32::MAX  →  15 < 4294967295  →  true  →  Err(AssertHeightRelativeFailed)
```

The same attack applies to `seconds_relative` using `u64::MAX - coin_timestamp + 1` as the crafted value. [1](#0-0) [2](#0-1)

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

**File:** crates/chia-consensus/src/conditions.rs (L582-596)
```rust
        ASSERT_SECONDS_RELATIVE => {
            maybe_check_args_terminator(a, c, flags)?;
            let node = first(a, c)?;
            match sanitize_uint(
                a,
                node,
                8,
                ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed),
            )? {
                SanitizedUint::PositiveOverflow => {
                    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed))
                }
                SanitizedUint::NegativeOverflow => Ok(Condition::SkipRelativeCondition),
                SanitizedUint::Ok(r) => Ok(Condition::AssertSecondsRelative(r)),
            }
```

**File:** crates/chia-consensus/src/conditions.rs (L614-628)
```rust
        ASSERT_HEIGHT_RELATIVE => {
            maybe_check_args_terminator(a, c, flags)?;
            let node = first(a, c)?;
            match sanitize_uint(
                a,
                node,
                4,
                ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed),
            )? {
                SanitizedUint::PositiveOverflow => {
                    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed))
                }
                SanitizedUint::NegativeOverflow => Ok(Condition::SkipRelativeCondition),
                SanitizedUint::Ok(r) => Ok(Condition::AssertHeightRelative(r as u32)),
            }
```
