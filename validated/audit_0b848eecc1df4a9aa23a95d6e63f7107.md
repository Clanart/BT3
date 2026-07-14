### Title
Relative Timelock Bypass via Integer Overflow in `check_time_locks` (`nowrap=false` Legacy Path) - (File: `crates/chia-consensus/src/check_time_locks.rs`)

### Summary
When `check_time_locks` is called with `nowrap=false` (the pre-soft-fork legacy path), relative timelock arithmetic uses `wrapping_add` instead of `saturating_add`. An unprivileged coin owner can craft a puzzle that emits `ASSERT_HEIGHT_RELATIVE(u32::MAX)` or `ASSERT_SECONDS_RELATIVE(u64::MAX)`, causing the addition to wrap around to a small value and making the timelock check pass immediately — bypassing a timelock that should require billions of blocks or seconds.

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` enforces relative timelocks for each spend in a bundle. The function accepts a `nowrap: bool` parameter that selects between two arithmetic modes:

- `nowrap=true` (post-soft-fork): uses `saturating_add`, so `confirmed_height.saturating_add(u32::MAX)` clamps to `u32::MAX`, and the check `prev_height < u32::MAX` correctly fails.
- `nowrap=false` (legacy path): uses `wrapping_add`, so `confirmed_height.wrapping_add(u32::MAX)` wraps to `confirmed_height - 1`, and the check `prev_height < confirmed_height - 1` passes immediately at any reasonable block height.

The relevant code paths are:

```rust
// ASSERT_HEIGHT_RELATIVE — nowrap=false branch
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}
```

```rust
// ASSERT_SECONDS_RELATIVE — nowrap=false branch
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
    return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
}
```

The codebase's own test suite explicitly documents and confirms the bypass:

```
// 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::height_relative_wrap(
    Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
    Ok(()),   // <-- bypass: passes when nowrap=false
)]
```

The same wrapping bypass applies to `ASSERT_SECONDS_RELATIVE(u64::MAX)`.

### Impact Explanation

An attacker who controls a puzzle (i.e., any coin owner) can emit `ASSERT_HEIGHT_RELATIVE(0xffff_ffff)` or `ASSERT_SECONDS_RELATIVE(0xffff_ffff_ffff_ffff)` as a condition. On nodes running the legacy `nowrap=false` path (for blocks before the soft-fork activation height), the timelock check wraps around and passes immediately. The coin is accepted as spendable at any block height, bypassing the intended timelock entirely. This constitutes a condition validation bypass enabling unauthorized spend acceptance — matching the **High** impact tier: *"timelock or coin-id validation bypass enables unauthorized spend acceptance."*

### Likelihood Explanation

The `nowrap=false` path remains active in production for all blocks below the soft-fork activation height. The Python full node passes `nowrap` as a caller-controlled boolean to `py_check_time_locks`. Any unprivileged user can create a coin with a puzzle that outputs a max-value relative timelock condition and immediately spend it on a node running the legacy path. No privileged access, leaked keys, or network-level attack is required — only the ability to submit a spend bundle.

### Recommendation

1. Remove the `nowrap=false` branch entirely from `check_time_locks`. The wrapping behavior is semantically incorrect for any timelock: a relative lock of `u32::MAX` blocks should never be satisfiable at any realistic chain height.
2. If backward compatibility with pre-fork blocks is required, enforce `nowrap=true` unconditionally in the Rust implementation and document that the old wrapping behavior was a consensus bug.
3. Add a consensus-level check during condition parsing (in `conditions.rs`) that rejects `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` values that would overflow when added to any plausible coin birth height/timestamp, rather than deferring the arithmetic to `check_time_locks`.

### Proof of Concept

Craft a coin whose puzzle outputs:
```
(ASSERT_HEIGHT_RELATIVE 0xffffffff)
```
Confirm the coin at block height 100. Submit a spend bundle at block height 101 (or any height). On a node calling `check_time_locks(..., nowrap=false)`:

```
confirmed_block_index = 100
height_relative       = 0xffff_ffff
wrapping sum          = 100u32.wrapping_add(0xffff_ffff) = 99
check: 101 < 99  →  false  →  no error returned  →  spend accepted
```

The spend is accepted immediately despite the puzzle encoding a ~4.3 billion block timelock. The same construction with `ASSERT_SECONDS_RELATIVE(0xffff_ffff_ffff_ffff)` bypasses the seconds-based timelock on the same path.

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
