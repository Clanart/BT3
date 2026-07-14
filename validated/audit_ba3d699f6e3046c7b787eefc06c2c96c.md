### Title
Integer Overflow in Relative Timelock Arithmetic Enables Timelock Bypass — (File: `crates/chia-consensus/src/check_time_locks.rs`)

### Summary
The `check_time_locks` function uses `wrapping_add` when `nowrap=false` for all four relative timelock conditions. An unprivileged CLVM puzzle can supply an attacker-chosen `height_relative` or `seconds_relative` value that overflows to a small number, causing the `ASSERT_HEIGHT_RELATIVE` / `ASSERT_SECONDS_RELATIVE` check to pass immediately and the `ASSERT_BEFORE_HEIGHT_RELATIVE` / `ASSERT_BEFORE_SECONDS_RELATIVE` check to fail spuriously — both are incorrect timelock evaluations reachable from a normal spend bundle.

### Finding Description
In `check_time_locks`, when `nowrap=false`, all four relative timelock branches use `wrapping_add` instead of `saturating_add`:

```rust
// ASSERT_HEIGHT_RELATIVE — line 64-68
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}

// ASSERT_SECONDS_RELATIVE — line 75-77
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
    return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
}

// ASSERT_BEFORE_HEIGHT_RELATIVE — line 90-98
} else if prev_transaction_block_height
    >= unspent.confirmed_block_index.wrapping_add(before_height_relative)
{ ... }

// ASSERT_BEFORE_SECONDS_RELATIVE — line 107-111
} else if timestamp >= unspent.timestamp.wrapping_add(before_seconds_relative)
{ ... }
``` [1](#0-0) 

The overflow is attacker-controlled: the `height_relative` / `seconds_relative` value comes directly from the CLVM output of the puzzle being spent. An attacker picks a value such that `confirmed_block_index.wrapping_add(height_relative)` overflows to 0 (or any value ≤ `prev_height`), making the `<` comparison false and the timelock pass immediately.

Concrete example for `ASSERT_HEIGHT_RELATIVE`:
- Coin confirmed at height `H = 100`
- Attacker sets `height_relative = u32::MAX − H + 1 = 0xffff_ff9c`
- `100u32.wrapping_add(0xffff_ff9c) = 0` (wraps to 0)
- Check: `prev_height < 0` → always `false` for any u32 `prev_height`
- Result: timelock passes at any block height

The project's own unit tests explicitly document and confirm this divergence:

```rust
// 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::height_relative_wrap(
    Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),  // nowrap=true
    Ok(()),                                                           // nowrap=false ← bypass
)]
``` [2](#0-1) 

The same overflow applies to `ASSERT_SECONDS_RELATIVE` (u64 wrapping):

```rust
// 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::seconds_relative_wrap(
    Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
    Ok(()),  // ← bypass
)]
``` [3](#0-2) 

And the inverse for `ASSERT_BEFORE_HEIGHT_RELATIVE` — wrapping causes a valid spend to be incorrectly rejected:

```rust
// 200 >= 100 + 0xffff_ffff -> Ok with nowrap (saturates), Err without (wraps)
#[case::before_height_relative_wrap(
    Osc { before_height_relative: Some(0xffff_ffff), ..Default::default() },
    Ok(()),                                                              // nowrap=true
    Err(ValidationErr::Err(ErrorCode::AssertBeforeHeightRelativeFailed)), // nowrap=false ← wrong
)]
``` [4](#0-3) 

The `nowrap` parameter is caller-supplied and exposed directly through the Python binding `py_check_time_locks`: [5](#0-4) 

The condition values themselves originate from the CLVM puzzle output parsed in `parse_args` / `parse_conditions`: [6](#0-5) 

### Impact Explanation
When `nowrap=false` is active in any consensus path, an attacker who controls a CLVM puzzle can set `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` to a crafted large value that wraps to 0, causing the timelock check to pass at any block height or timestamp. This is a **timelock validation bypass enabling unauthorized spend acceptance**: a coin that should be unspendable until a future height/time can be spent immediately. This matches the allowed High impact: *"condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance."*

Additionally, if nodes disagree on the `nowrap` flag value, the same spend bundle produces opposite validity outcomes on different nodes, constituting deterministic consensus divergence (Critical impact).

### Likelihood Explanation
The attacker's entry path requires only a valid CLVM puzzle — no privileged access, leaked keys, or governance control. The `nowrap=false` path is the legacy consensus behavior (pre-hard-fork), and the Python binding exposes `nowrap` as a plain boolean parameter. Any full-node code path that calls `check_time_locks` with `nowrap=False` for blocks where attacker-controlled timelocks are accepted is directly exploitable.

### Recommendation
Replace `wrapping_add` with `saturating_add` unconditionally for all four relative timelock branches, or remove the `nowrap=false` code path entirely if the legacy wrapping behavior is no longer required for any live consensus path. The `nowrap` flag should not be a caller-controlled boolean that silently changes the security semantics of timelock enforcement.

### Proof of Concept
1. Observe a coin confirmed at block height `H` (e.g., `H = 100`).
2. Craft a CLVM puzzle that outputs condition `(ASSERT_HEIGHT_RELATIVE . (u32::MAX − H + 1))` — e.g., `(82 . 0xffff_ff9c)` for `H=100`.
3. Submit a spend bundle containing this coin spend to a node running `check_time_locks` with `nowrap=false`.
4. Inside `check_time_locks`:
   - `100u32.wrapping_add(0xffff_ff9c) = 0`
   - Check: `prev_height < 0` → `false` for any u32 `prev_height`
   - `AssertHeightRelativeFailed` is **not** returned; the spend is accepted.
5. The coin is spendable at block height 0 (i.e., immediately), bypassing the intended timelock entirely.

### Citations

**File:** crates/chia-consensus/src/check_time_locks.rs (L55-112)
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

**File:** crates/chia-consensus/src/check_time_locks.rs (L302-306)
```rust
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
