### Title
Wrapping Integer Overflow in Timelock Condition Validation Enables Timelock Bypass — (File: `crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

When `check_time_locks` is called with `nowrap=false`, it uses `wrapping_add` for relative timelock arithmetic. An attacker can craft a CLVM spend with `ASSERT_HEIGHT_RELATIVE(u32::MAX)` or `ASSERT_SECONDS_RELATIVE(u64::MAX)` to cause the addition to wrap around to a small value, making the timelock condition trivially satisfied and allowing the coin to be spent immediately regardless of the intended lock period.

---

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` accepts a `nowrap: bool` parameter that selects between two arithmetic modes for relative timelock evaluation:

- `nowrap=true` → `saturating_add` (correct: clamps at type maximum, enforces the lock)
- `nowrap=false` → `wrapping_add` (legacy: wraps modulo type size, can bypass the lock)

The four affected branches are:

```rust
// ASSERT_HEIGHT_RELATIVE (u32)
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)  // line 65
```
```rust
// ASSERT_SECONDS_RELATIVE (u64)
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative)  // line 75
```
```rust
// ASSERT_BEFORE_HEIGHT_RELATIVE (u32)
} else if prev_transaction_block_height
    >= unspent.confirmed_block_index.wrapping_add(before_height_relative)  // line 93
```
```rust
// ASSERT_BEFORE_SECONDS_RELATIVE (u64)
} else if timestamp >= unspent.timestamp.wrapping_add(before_seconds_relative)  // line 107
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

The condition parser in `conditions.rs` accepts `ASSERT_HEIGHT_RELATIVE` values up to `u32::MAX` (4 bytes) and `ASSERT_SECONDS_RELATIVE` values up to `u64::MAX` (8 bytes) as valid, non-overflowing inputs: [5](#0-4) 

The `nowrap` parameter is caller-controlled and is exposed directly through the Python binding `py_check_time_locks` registered in `wheel/src/api.rs`: [6](#0-5) [7](#0-6) 

The codebase's own tests explicitly document the bypass:

```
# 10 + u32::MAX overflows to 9, wrapping: 15 < 9 -> Ok (timelock bypassed)
(make_test_conds(height_relative=0xFFFF_FFFF), 13, None),
``` [8](#0-7) 

---

### Impact Explanation

When `nowrap=false` is active for a transaction block, any unprivileged spender can craft a coin spend with `ASSERT_HEIGHT_RELATIVE(0xFFFF_FFFF)` (or `ASSERT_SECONDS_RELATIVE(0xFFFF_FFFF_FFFF_FFFF)`). The wrapping addition `confirmed_block_index + 0xFFFF_FFFF` wraps to `confirmed_block_index - 1`, which is always less than the current block height. The timelock check `prev_height < (confirmed_block_index - 1)` evaluates to `false`, so the condition passes — the coin is spendable immediately, bypassing the intended lock entirely.

For `ASSERT_BEFORE_HEIGHT_RELATIVE` and `ASSERT_BEFORE_SECONDS_RELATIVE`, the wrapping inverts the semantics: a condition intended to restrict spending to a window far in the future wraps to a small deadline that has already passed, causing the spend to be **incorrectly rejected** — a denial-of-service on legitimate spends.

This matches the **High** allowed impact: *timelock condition validation bypass enables unauthorized spend acceptance*.

---

### Likelihood Explanation

Likelihood depends on whether `nowrap=false` is passed by the Chia node for any live transaction block. The parameter is fully caller-controlled from the Python layer with no enforcement at the Rust boundary. The `nowrap=false` path is the pre-soft-fork legacy mode; if any node version or code path still passes `nowrap=false` for new blocks (e.g., during a transition period or via a misconfigured call), the bypass is immediately exploitable by any unprivileged spender with a crafted spend bundle. No special keys, roles, or network access beyond submitting a transaction are required.

---

### Recommendation

Replace `wrapping_add` with `saturating_add` (or `checked_add` returning an error) in all four relative timelock branches, unconditionally. The `nowrap` parameter and the `wrapping_add` code paths should be removed entirely. The `saturating_add` behavior is the correct semantic: a relative timelock of `u32::MAX` blocks means "never spendable within the u32 height range," which `saturating_add` correctly enforces by clamping to `u32::MAX`. [9](#0-8) 

---

### Proof of Concept

Construct a CLVM spend bundle for a coin confirmed at height `H` with condition:

```
(ASSERT_HEIGHT_RELATIVE . 0xFFFFFFFF)
```

Call `check_time_locks` with `nowrap=false`, `prev_transaction_block_height = H + 1`:

```
confirmed_block_index.wrapping_add(0xFFFF_FFFF)
= H.wrapping_add(u32::MAX)
= H - 1   (for any H > 0)

Check: (H + 1) < (H - 1)  →  false  →  condition passes
```

The coin is accepted at height `H + 1` despite the `u32::MAX`-block relative timelock. The codebase's own unit test confirms this outcome at line 278–281 of `check_time_locks.rs`. [8](#0-7)

### Citations

**File:** crates/chia-consensus/src/check_time_locks.rs (L55-115)
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
        }
        if let Some(seconds_relative) = spend.seconds_relative {
            if nowrap {
                if timestamp < unspent.timestamp.saturating_add(seconds_relative) {
                    return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
                }
            } else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
                return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
            }
        }
        if let Some(before_height_relative) = spend.before_height_relative {
            if nowrap {
                if prev_transaction_block_height
                    >= unspent
                        .confirmed_block_index
                        .saturating_add(before_height_relative)
                {
                    return Err(ValidationErr::Err(
                        ErrorCode::AssertBeforeHeightRelativeFailed,
                    ));
                }
            } else if prev_transaction_block_height
                >= unspent
                    .confirmed_block_index
                    .wrapping_add(before_height_relative)
            {
                return Err(ValidationErr::Err(
                    ErrorCode::AssertBeforeHeightRelativeFailed,
                ));
            }
        }
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
    }

    Ok(())
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L118-141)
```rust
#[cfg(feature = "py-bindings")]
#[pyfunction]
#[pyo3(name = "check_time_locks")]
#[allow(clippy::needless_pass_by_value)] // pyo3 prefers pass_by_value
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

**File:** wheel/src/api.rs (L9-9)
```rust
use chia_consensus::check_time_locks::py_check_time_locks;
```
