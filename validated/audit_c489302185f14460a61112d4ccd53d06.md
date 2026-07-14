### Title
Relative Timelock Bypass via Integer Overflow in `check_time_locks` When `nowrap = false` - (File: `crates/chia-consensus/src/check_time_locks.rs`)

### Summary
When `check_time_locks` is invoked with `nowrap = false` (legacy mode), the relative timelock checks for `ASSERT_HEIGHT_RELATIVE` and `ASSERT_SECONDS_RELATIVE` use `wrapping_add` instead of `saturating_add`. A crafted coin whose puzzle emits a maximum-value relative timelock condition (e.g., `ASSERT_HEIGHT_RELATIVE 0xFFFFFFFF`) causes the addition to wrap around to a value smaller than the current block height, making the guard trivially pass and allowing the coin to be spent immediately — bypassing the intended timelock entirely.

### Finding Description

`check_time_locks` enforces relative timelocks by comparing the current block height (or timestamp) against the coin's confirmation height (or timestamp) plus the required relative delay:

```
prev_transaction_block_height < unspent.confirmed_block_index.wrapping_add(height_relative)
```

When `nowrap = false`, `wrapping_add` is used. If `height_relative = 0xFFFF_FFFF` and `confirmed_block_index = 100`, the addition wraps:

```
100u32.wrapping_add(0xFFFF_FFFF) = 99
```

The guard becomes `prev_height < 99`. At any block height ≥ 99 the check passes (no error), so the coin is spendable immediately rather than after ~4 billion blocks. The same overflow applies to `seconds_relative` with `u64::MAX`.

The repository's own test suite explicitly documents this divergence:

```
// 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::height_relative_wrap(
    Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
    Ok(()),   // ← wrapping path silently accepts the spend
)]
```

The same pattern exists for `seconds_relative`:

```
// 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::seconds_relative_wrap(
    Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
    Ok(()),   // ← wrapping path silently accepts the spend
)]
```

### Impact Explanation

A coin whose Chialisp puzzle outputs `(ASSERT_HEIGHT_RELATIVE . 0xFFFFFFFF)` or `(ASSERT_SECONDS_RELATIVE . 0xFFFFFFFFFFFFFFFF)` is intended to be unspendable for an astronomically long time. Under the `nowrap = false` path the overflow makes the guard trivially satisfied, so the coin can be spent at any ordinary block height or timestamp. This is a consensus-level timelock validation bypass that enables unauthorized spend acceptance — matching the **High** impact category: *"timelock … validation bypass enables unauthorized spend acceptance."*

### Likelihood Explanation

- The `nowrap` parameter is caller-controlled and the `nowrap = false` branch is explicitly preserved and tested, indicating it is reachable in production (e.g., for pre-hard-fork block validation or via the Python binding `py_check_time_locks`).
- The attacker-controlled entry path is a standard unprivileged CLVM puzzle that emits a single `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` condition with a max-value argument — no special privileges required.
- The condition argument is parsed and stored as a plain integer before being passed to `check_time_locks`, so no upstream sanitization prevents the max value from reaching the vulnerable arithmetic.

### Recommendation

Replace `wrapping_add` with `saturating_add` unconditionally in all four relative-timelock branches, or remove the `nowrap = false` code path entirely if it is no longer needed for any live block-validation context. The `nowrap = true` (saturating) path already produces the correct result for all inputs.

### Proof of Concept

1. Craft a Chialisp puzzle that outputs the condition list `((82 . 0xFFFFFFFF))` (`ASSERT_HEIGHT_RELATIVE 4294967295`).
2. Create a coin with this puzzle hash and confirm it at block height 100.
3. Submit a spend of that coin at block height 200, validated with `nowrap = false`.
4. `check_time_locks` computes `100u32.wrapping_add(0xFFFF_FFFF) = 99`; the guard `200 < 99` is false, so no error is returned and the spend is accepted — despite the coin being intended to be locked for ~4 billion more blocks.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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
