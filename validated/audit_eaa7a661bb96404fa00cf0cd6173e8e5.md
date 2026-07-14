### Title
Integer Wrapping in `check_time_locks` Bypasses `ASSERT_SECONDS_RELATIVE` / `ASSERT_HEIGHT_RELATIVE` Timelocks When `nowrap=false` - (File: `crates/chia-consensus/src/check_time_locks.rs`)

### Summary

`check_time_locks` contains two arithmetic code paths controlled by the `nowrap` boolean parameter. When `nowrap=false`, relative timelock additions use `wrapping_add` instead of `saturating_add`. An attacker can craft a CLVM puzzle with `ASSERT_SECONDS_RELATIVE(u64::MAX)` (a valid, parseable condition value). At validation time, `coin_timestamp.wrapping_add(u64::MAX)` evaluates to `coin_timestamp - 1`, making the guard `timestamp < coin_timestamp - 1` permanently false for any spend occurring after coin creation. The timelock is silently bypassed and the coin is accepted as spendable immediately.

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` enforces relative timelocks for `ASSERT_SECONDS_RELATIVE`, `ASSERT_HEIGHT_RELATIVE`, `ASSERT_BEFORE_SECONDS_RELATIVE`, and `ASSERT_BEFORE_HEIGHT_RELATIVE` conditions. The function takes a `nowrap: bool` parameter that selects between two arithmetic strategies:

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
``` [1](#0-0) 

The same pattern applies to `height_relative`:

```rust
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
``` [2](#0-1) 

The condition parser accepts `seconds_relative` values up to `u64::MAX` as valid. `ASSERT_SECONDS_RELATIVE` with `max_size=8` in `sanitize_uint` allows a 9-byte CLVM atom `0x00FFFFFFFFFFFFFFFF` (leading zero to avoid sign bit), which decodes to `u64::MAX` and is returned as `SanitizedUint::Ok(u64::MAX)`: [3](#0-2) [4](#0-3) 

The test suite explicitly documents and confirms the bypass:

```
// 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::seconds_relative_wrap(
    Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
    Ok(()),   // <-- bypass: no error returned
)]
``` [5](#0-4) 

The Python test suite confirms the same behavior end-to-end: [6](#0-5) 

The Python binding exposes `nowrap` as a caller-controlled parameter: [7](#0-6) 

### Impact Explanation

When `nowrap=false` is passed by the full node (the legacy/pre-fork validation path), an attacker who has created a coin whose puzzle contains `ASSERT_SECONDS_RELATIVE(u64::MAX)` can spend it immediately:

- `coin_timestamp.wrapping_add(u64::MAX)` = `coin_timestamp - 1`
- Guard: `current_timestamp < coin_timestamp - 1` → **always false** for any spend after creation
- Result: the timelock condition is silently satisfied; the spend is accepted as consensus-valid

This is a **timelock validation bypass enabling unauthorized spend acceptance**, matching the High impact category. The same arithmetic flaw applies to `ASSERT_HEIGHT_RELATIVE(u32::MAX)`:

- `confirmed_block_index.wrapping_add(u32::MAX)` = `confirmed_block_index - 1`
- Guard: `prev_height < confirmed_block_index - 1` → false for any spend after confirmation [8](#0-7) 

The inverse wrapping effect on `ASSERT_BEFORE_SECONDS_RELATIVE` / `ASSERT_BEFORE_HEIGHT_RELATIVE` causes the opposite: a spend that should be valid is permanently rejected (coin locked forever), which is a secondary impact: [9](#0-8) 

### Likelihood Explanation

The `nowrap` parameter is passed from the Python full node (chia-blockchain, external to this repo). The `nowrap=false` path is the legacy pre-fork behavior. Any block height range for which the full node passes `nowrap=false` is exploitable. The attacker-controlled inputs are entirely within the CLVM puzzle: `seconds_relative = u64::MAX` is a valid, parseable condition value requiring no privileged access. The exploit is deterministic and requires no timing, key material, or network-level attack.

### Recommendation

Replace `wrapping_add` with `saturating_add` unconditionally in all four relative timelock checks, or remove the `nowrap=false` code path entirely if it is no longer needed for any active block height range. If backward compatibility with old blocks is required, document the exact height range for which `nowrap=false` is used and ensure no coins with overflow-inducing `seconds_relative` / `height_relative` values exist in that range.

```rust
// Replace:
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {

// With:
} else if timestamp < unspent.timestamp.saturating_add(seconds_relative) {
``` [1](#0-0) 

### Proof of Concept

1. Craft a CLVM puzzle that emits `(ASSERT_SECONDS_RELATIVE 0x00FFFFFFFFFFFFFFFF)` — the 9-byte encoding of `u64::MAX`, accepted by `sanitize_uint` with `max_size=8`.
2. Create a coin locked by this puzzle. The coin's `confirmed_block_index` timestamp is `T`.
3. Call `check_time_locks(..., nowrap=false)` at any time after creation.
4. Internally: `T.wrapping_add(u64::MAX) = T - 1`. Guard: `current_timestamp < T - 1` → false (since `current_timestamp >= T`). No error is returned.
5. The spend is accepted as consensus-valid despite the intended multi-billion-year timelock.

The test suite already encodes this exact scenario and confirms `Ok(())` (no error) is returned when `nowrap=false`: [10](#0-9) [5](#0-4)

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

**File:** crates/chia-consensus/src/sanitize_int.rs (L43-51)
```rust
    // strip the leading zero byte if there is one
    let size_limit = if buf[0] == 0 { max_size + 1 } else { max_size };

    // if there are too many bytes left in the value, it's too big
    if buf.len() > size_limit {
        return Ok(SanitizedUint::PositiveOverflow);
    }

    Ok(SanitizedUint::Ok(u64_from_bytes(buf)))
```

**File:** tests/test_check_time_locks.py (L244-251)
```python
            # --- seconds_relative wrapping ---
            # coin_timestamp=10000, prev_timestamp=10150
            # 10000 + (u64::MAX - 10000) = u64::MAX, no overflow -> Err both
            (make_test_conds(seconds_relative=0xFFFF_FFFF_FFFF_D8EF), 105, 105),
            # 10000 + (u64::MAX - 9999) overflows to 0, wrapping: 10150 < 0 -> Ok
            (make_test_conds(seconds_relative=0xFFFF_FFFF_FFFF_D8F0), 105, None),
            # 10000 + u64::MAX overflows to 9999, wrapping: 10150 < 9999 -> Ok
            (make_test_conds(seconds_relative=0xFFFF_FFFF_FFFF_FFFF), 105, None),
```

**File:** wheel/python/chia_rs/chia_rs.pyi (L40-46)
```text
def check_time_locks(
    removal_coin_records: dict[bytes32, CoinRecord],
    bundle_conds: SpendBundleConditions,
    prev_transaction_block_height: uint32,
    timestamp: uint64,
    nowrap: bool,
) -> Optional[int]: ...
```
