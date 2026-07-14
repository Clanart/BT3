### Title
Timelock Validation Bypass via Integer Wrapping in `check_time_locks` (`nowrap=false` Path) — (`File: crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` contains two arithmetic paths controlled by the `nowrap: bool` parameter. When `nowrap=false`, relative timelock comparisons use Rust's `wrapping_add` instead of `saturating_add`. An attacker who controls a CLVM puzzle can emit an `ASSERT_SECONDS_RELATIVE` (or `ASSERT_HEIGHT_RELATIVE`) condition with a crafted near-`u64::MAX` value that wraps the deadline back to a small integer, causing the timelock check to pass immediately — bypassing the intended lock period entirely.

---

### Finding Description

`check_time_locks` accepts a `nowrap: bool` flag. When `nowrap=false`, the four relative-timelock branches use `wrapping_add`:

```rust
// seconds_relative — line 75
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
    return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
}

// height_relative — line 65
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}
```

The check is: **reject the spend if `current_time < coin_time + seconds_relative`**. When `coin_time + seconds_relative` overflows `u64`, `wrapping_add` produces a value smaller than `coin_time`. For example:

| `coin_time` | `seconds_relative` | `wrapping_add` result | `current_time < result`? | Outcome |
|---|---|---|---|---|
| 10 000 | `u64::MAX` | 9 999 | `10 150 < 9 999` → **false** | **Spend accepted** |
| 10 000 | `u64::MAX − 9 999` | 0 | `10 150 < 0` → **false** | **Spend accepted** |

The test suite explicitly documents and confirms this behavior:

```
// 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::seconds_relative_wrap(
    Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
    Ok(()),   // <-- nowrap=false: spend ACCEPTED despite huge timelock
)]
```

The same wrapping flaw exists for `height_relative` (u32 wrapping), and the inverse direction (`before_height_relative`, `before_seconds_relative`) wraps to a small value that causes a spurious rejection — a DoS on coins that should be spendable.

The `seconds_relative` value is emitted by the CLVM puzzle program. Any party who controls the puzzle (the coin's locking script) can set this value to `u64::MAX − coin_timestamp + 1` or any value that causes the sum to wrap below the current timestamp.

---

### Impact Explanation

**High — timelock condition validation bypass enables unauthorized spend acceptance.**

A coin whose puzzle emits `ASSERT_SECONDS_RELATIVE 0xFFFFFFFFFFFFFFFF` appears to be locked for ~584 billion years. Under `nowrap=false` validation, `coin_timestamp.wrapping_add(0xFFFFFFFFFFFFFFFF)` wraps to `coin_timestamp − 1`, which is always less than the current timestamp, so the check `timestamp < (coin_timestamp − 1)` is `false` and the spend is **accepted immediately**. Any party relying on the timelock as a security guarantee (e.g., as collateral, a vesting schedule, or a HTLC) is deceived: the coin is spendable at any time.

---

### Likelihood Explanation

The `nowrap` parameter is exposed through the Python binding `py_check_time_locks` and is called from the Chia full node in Python. The `nowrap=false` path is a live, reachable code path (not dead code): it is parametrically tested in both the Rust unit tests and the Python test suite (`tests/test_check_time_locks.py`). An attacker only needs to craft a CLVM puzzle that outputs a near-`u64::MAX` `ASSERT_SECONDS_RELATIVE` atom — a trivial, unprivileged operation requiring no special access.

---

### Recommendation

Replace `wrapping_add` with `saturating_add` unconditionally for all four relative-timelock branches, or remove the `nowrap=false` code path entirely if it is no longer needed for historical block replay. If backward compatibility with pre-fork blocks is required, gate the wrapping path strictly on block height (not on a caller-supplied boolean) so it cannot be triggered for new spend bundles.

```rust
// Replace:
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {

// With:
if timestamp < unspent.timestamp.saturating_add(seconds_relative) {
```

---

### Proof of Concept

**Setup:** Coin confirmed at `timestamp = 10_000`. Current block `timestamp = 10_150`. Attacker's puzzle emits:

```
(ASSERT_SECONDS_RELATIVE 0xFFFFFFFFFFFFFFFF)
```

**Arithmetic under `nowrap=false`:**

```
10_000u64.wrapping_add(0xFFFF_FFFF_FFFF_FFFF)
= 10_000 + 18_446_744_073_709_541_615 (mod 2^64)
= 9_999
```

**Check:** `10_150 < 9_999` → `false` → **no error returned → spend accepted**.

This is confirmed by the existing test case `seconds_relative_wrap` in `check_time_locks.rs` (line 302–306) and `test_wrapping_conditions` in `tests/test_check_time_locks.py` (line 249–251), both of which assert `Ok(())` (spend accepted) when `nowrap=false` and `seconds_relative = u64::MAX`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/chia-consensus/src/check_time_locks.rs (L12-18)
```rust
pub fn check_time_locks(
    removal_coin_records: &HashMap<Bytes32, CoinRecord>,
    bundle_conds: &OwnedSpendBundleConditions,
    prev_transaction_block_height: u32,
    timestamp: u64,
    nowrap: bool,
) -> Result<(), ValidationErr> {
```

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

**File:** crates/chia-consensus/src/check_time_locks.rs (L301-306)
```rust
    // 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
    #[case::seconds_relative_wrap(
        Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
        Ok(()),
    )]
```

**File:** tests/test_check_time_locks.py (L247-251)
```python
            (make_test_conds(seconds_relative=0xFFFF_FFFF_FFFF_D8EF), 105, 105),
            # 10000 + (u64::MAX - 9999) overflows to 0, wrapping: 10150 < 0 -> Ok
            (make_test_conds(seconds_relative=0xFFFF_FFFF_FFFF_D8F0), 105, None),
            # 10000 + u64::MAX overflows to 9999, wrapping: 10150 < 9999 -> Ok
            (make_test_conds(seconds_relative=0xFFFF_FFFF_FFFF_FFFF), 105, None),
```
