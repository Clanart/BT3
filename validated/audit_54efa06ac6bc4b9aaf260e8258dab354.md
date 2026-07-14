### Title
Integer Overflow in Relative Timelock Arithmetic Enables Timelock Bypass or Permanent Coin Lock — (`File: crates/chia-consensus/src/check_time_locks.rs`)

### Summary

`check_time_locks` uses `wrapping_add` when `nowrap=false` to compute the deadline for `ASSERT_HEIGHT_RELATIVE`, `ASSERT_SECONDS_RELATIVE`, `ASSERT_BEFORE_HEIGHT_RELATIVE`, and `ASSERT_BEFORE_SECONDS_RELATIVE` conditions. An attacker-controlled near-maximum condition value causes the sum to wrap around to a small integer, producing the opposite validation outcome: a relative height/time lock that should block a spend instead passes, or a "must-spend-before" guard that should allow a spend instead permanently rejects it.

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` accepts a `nowrap: bool` parameter. When `nowrap=false`, the relative timelock deadline is computed with `wrapping_add`:

```rust
// ASSERT_HEIGHT_RELATIVE path (nowrap=false)
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}
``` [1](#0-0) 

And for `ASSERT_BEFORE_HEIGHT_RELATIVE`:

```rust
} else if prev_transaction_block_height
    >= unspent
        .confirmed_block_index
        .wrapping_add(before_height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertBeforeHeightRelativeFailed));
}
``` [2](#0-1) 

The same pattern applies to `seconds_relative` and `before_seconds_relative` using `u64::wrapping_add`: [3](#0-2) [4](#0-3) 

The overflow behavior is explicitly confirmed by the codebase's own test cases:

```
// 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::height_relative_wrap(
    Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
    Ok(()),   // <-- wrapping path: spend PASSES when it should be BLOCKED
)]
``` [5](#0-4) 

```
// 200 >= 100 + 0xffff_ffff -> Ok with nowrap (saturates), Err without (wraps)
#[case::before_height_relative_wrap(
    Osc { before_height_relative: Some(0xffff_ffff), ..Default::default() },
    Ok(()),
    Err(ValidationErr::Err(ErrorCode::AssertBeforeHeightRelativeFailed)),  // <-- permanently locked
)]
``` [6](#0-5) 

The Python binding exposes `nowrap` as a caller-controlled boolean, meaning any Python node code that invokes `check_time_locks` with `nowrap=False` is subject to this behavior: [7](#0-6) 

### Impact Explanation

Two distinct impacts arise from the wrapping arithmetic:

1. **Timelock bypass (High):** A coin locked with `ASSERT_HEIGHT_RELATIVE = 0xffff_ffff` (or any value that causes `confirmed_block_index + height_relative` to wrap below `prev_height`) is accepted as spendable at any block height when `nowrap=false`. The relative height lock is completely bypassed. The same applies to `ASSERT_SECONDS_RELATIVE` with a near-`u64::MAX` value.

2. **Permanent coin lock (High):** A coin with `ASSERT_BEFORE_HEIGHT_RELATIVE = 0xffff_ffff` wraps the deadline to a value smaller than any realistic `prev_height`, causing the guard to always fire and permanently preventing the coin from being spent. This maps directly to the external report's "stuck" asset pattern.

Both outcomes are reachable via unprivileged spend bundle input: the attacker simply sets the condition argument to a crafted near-maximum value. No privileged role or key is required.

### Likelihood Explanation

The `nowrap` parameter is a caller-controlled boolean exposed through the Python wheel API. Any chia-blockchain Python code path that calls `check_time_locks(..., nowrap=False)` — including mempool validation for pre-fork blocks or any node that has not yet adopted the saturating path — is vulnerable. The wrapping path is not gated by any consensus flag or height check inside `check_time_locks` itself; the entire guard is delegated to the caller's choice of `nowrap`. Because the Python API makes `nowrap=False` a valid and reachable call, and because the condition argument is fully attacker-controlled (it comes from the CLVM output of a spend bundle), the likelihood of exploitation is real wherever `nowrap=False` is in use.

### Recommendation

1. **Remove the `nowrap=false` / `wrapping_add` code path entirely.** The saturating behavior (`nowrap=true`) is the only arithmetically correct one for timelock semantics. Wrapping addition has no valid consensus interpretation for relative timelocks.
2. If backward compatibility with old blocks is required, gate the wrapping path on a block height threshold (hard-fork height) inside `check_time_locks` itself rather than delegating the choice to the caller.
3. Validate that condition arguments for `ASSERT_HEIGHT_RELATIVE` and `ASSERT_SECONDS_RELATIVE` cannot exceed `u32::MAX - confirmed_block_index` (resp. `u64::MAX - coin_timestamp`) at the condition-parsing layer, rejecting overflow-inducing values before they reach the timelock check.

### Proof of Concept

Using the existing test infrastructure (confirmed at height 100, checked at height 200):

```rust
// ASSERT_HEIGHT_RELATIVE = 0xffff_ffff, nowrap=false
// 100u32.wrapping_add(0xffff_ffff) = 99
// 200 < 99  →  false  →  Ok(())  ← spend accepted, timelock bypassed

// ASSERT_BEFORE_HEIGHT_RELATIVE = 0xffff_ffff, nowrap=false
// 100u32.wrapping_add(0xffff_ffff) = 99
// 200 >= 99  →  true  →  Err(AssertBeforeHeightRelativeFailed)  ← coin permanently locked
```

The codebase's own unit tests already document and assert these exact outcomes for `nowrap=false`: [5](#0-4) [8](#0-7) [6](#0-5) [9](#0-8)

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

**File:** crates/chia-consensus/src/check_time_locks.rs (L90-98)
```rust
            } else if prev_transaction_block_height
                >= unspent
                    .confirmed_block_index
                    .wrapping_add(before_height_relative)
            {
                return Err(ValidationErr::Err(
                    ErrorCode::AssertBeforeHeightRelativeFailed,
                ));
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

**File:** crates/chia-consensus/src/check_time_locks.rs (L326-331)
```rust
    // 200 >= 100 + 0xffff_ffff -> Ok with nowrap (saturates), Err without (wraps)
    #[case::before_height_relative_wrap(
        Osc { before_height_relative: Some(0xffff_ffff), ..Default::default() },
        Ok(()),
        Err(ValidationErr::Err(ErrorCode::AssertBeforeHeightRelativeFailed)),
    )]
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L351-356)
```rust
    // 2000 >= 1000 + u64::MAX -> Ok with nowrap (saturates), Err without (wraps)
    #[case::before_seconds_relative_wrap(
        Osc { before_seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
        Ok(()),
        Err(ValidationErr::Err(ErrorCode::AssertBeforeSecondsRelativeFailed)),
    )]
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
