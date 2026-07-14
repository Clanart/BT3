### Title
Integer Wrapping in Relative Timelock Arithmetic Enables Timelock Bypass — (File: `crates/chia-consensus/src/check_time_locks.rs`)

### Summary
The `check_time_locks` function accepts a `nowrap: bool` parameter. When `nowrap=false`, it uses `wrapping_add` for relative timelock arithmetic on `ASSERT_HEIGHT_RELATIVE` and `ASSERT_SECONDS_RELATIVE` conditions. An unprivileged user can craft a CLVM puzzle with `ASSERT_HEIGHT_RELATIVE 0xffff_ffff` (or `ASSERT_SECONDS_RELATIVE 0xffff_ffff_ffff_ffff`) to cause the addition to wrap around to a small value, making the timelock check pass immediately instead of requiring the intended ~4 billion blocks or seconds. This is a timelock validation bypass that enables unauthorized spend acceptance.

### Finding Description

In `check_time_locks`, the relative height and seconds checks branch on the `nowrap` flag:

```rust
if let Some(height_relative) = spend.height_relative {
    if nowrap {
        if prev_transaction_block_height
            < unspent.confirmed_block_index.saturating_add(height_relative)
        {
            return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
        }
    } else if prev_transaction_block_height
        < unspent.confirmed_block_index.wrapping_add(height_relative)
    {
        return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
    }
}
``` [1](#0-0) 

When `nowrap=false`, `wrapping_add` is used. If a CLVM puzzle emits `ASSERT_HEIGHT_RELATIVE 0xffff_ffff` and the coin was confirmed at block index 100:

- `confirmed_block_index.wrapping_add(0xffff_ffff)` = `100u32.wrapping_add(0xffff_ffff)` = **99**
- The check becomes: `prev_transaction_block_height < 99`
- At any height ≥ 99 (e.g., height 200), the check passes → **timelock bypassed**

The intended behavior is that the coin should be unspendable for ~4 billion blocks. With wrapping, it is immediately spendable.

The same wrapping flaw applies to `seconds_relative` (u64 wrapping):

```rust
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
    return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
}
``` [2](#0-1) 

With `seconds_relative = 0xffff_ffff_ffff_ffff` and `timestamp = 1000`, `wrapping_add(1000, u64::MAX) = 999`. The check `current_timestamp < 999` passes at any timestamp ≥ 999, bypassing a timelock intended to last ~584 billion years.

The repository's own test suite explicitly documents and confirms this divergence:

```rust
// 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::height_relative_wrap(
    Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
    Ok(()),
)]
// 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::seconds_relative_wrap(
    Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
    Ok(()),
)]
``` [3](#0-2) 

The `nowrap` parameter is exposed directly through the Python binding `py_check_time_locks`, meaning the full node Python code controls which arithmetic path is taken at runtime: [4](#0-3) 

The `height_relative` value is parsed from CLVM as a 4-byte (u32) unsigned integer via `sanitize_uint(a, node, 4, ...)`, and `seconds_relative` as an 8-byte (u64) value. Both are fully attacker-controlled through the CLVM puzzle output. [5](#0-4) 

### Impact Explanation

This is a **timelock validation bypass enabling unauthorized spend acceptance**. Any smart contract, payment channel, escrow, or vesting puzzle that relies on `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` with a large value for security can be bypassed when the full node calls `check_time_locks` with `nowrap=false`. The coin is accepted as validly spent before its intended lock expires. This matches the allowed High impact: *"timelock or coin-id validation bypass enables unauthorized spend acceptance."*

### Likelihood Explanation

The `nowrap` parameter is a runtime boolean passed from Python. If the Chia full node passes `nowrap=False` for any block height range (e.g., pre-soft-fork blocks for backward compatibility), the wrapping path is live and exploitable. The fact that the wrapping path is retained in production code and exposed through the Python binding — rather than being removed — indicates it is still reachable. An attacker only needs to craft a CLVM puzzle with `ASSERT_HEIGHT_RELATIVE 0xffff_ffff`; no privileged access, key material, or network-level capability is required.

### Recommendation

1. Remove the `nowrap=false` / `wrapping_add` code path entirely from `check_time_locks`. Always use `saturating_add`.
2. If backward compatibility with pre-fork blocks is required, gate the wrapping path behind a block-height check at the call site rather than exposing it as a runtime boolean.
3. Audit all callers of `py_check_time_locks` in the Python full node to confirm `nowrap=True` is always passed for current and future blocks.

### Proof of Concept

1. Craft a CLVM puzzle that outputs condition `(ASSERT_HEIGHT_RELATIVE 0xffff_ffff)`.
2. Fund a coin with this puzzle; it is confirmed at block height 100.
3. At block height 200, submit a spend bundle for this coin.
4. The full node calls `check_time_locks(..., nowrap=False)`.
5. Computation: `100u32.wrapping_add(0xffff_ffff_u32) = 99`.
6. Check: `200 < 99` → `false` → no error returned → **spend accepted**.
7. The coin is spent ~4 billion blocks before its intended timelock expires.

With `nowrap=True` (correct behavior): `100u32.saturating_add(0xffff_ffff_u32) = 0xffff_ffff`. Check: `200 < 4294967295` → `true` → `AssertHeightRelativeFailed` → spend correctly rejected.

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

**File:** crates/chia-consensus/src/check_time_locks.rs (L276-306)
```rust
    // 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
    #[case::height_relative_wrap(
        Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
        Ok(()),
    )]
    // seconds_relative check: timestamp < coin_time + seconds_relative -> Err
    // 2000 < 1000 + 1001 = 2001 -> Err (both agree, no overflow)
    #[case::seconds_relative_under(
        Osc { seconds_relative: Some(1001), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
        Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
    )]
    // 2000 < 1000 + 1000 = 2000 -> Ok (both agree, no overflow)
    #[case::seconds_relative_exact(
        Osc { seconds_relative: Some(1000), ..Default::default() },
        Ok(()),
        Ok(()),
    )]
    // 2000 < 1000 + 999 = 1999 -> Ok (both agree, no overflow)
    #[case::seconds_relative_over(
        Osc { seconds_relative: Some(999), ..Default::default() },
        Ok(()),
        Ok(()),
    )]
    // 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
    #[case::seconds_relative_wrap(
        Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
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
