### Title
Relative Timelock Integer Wrapping Bypass in `check_time_locks` — (`File: crates/chia-consensus/src/check_time_locks.rs`)

### Summary

`check_time_locks` contains a `nowrap` boolean that selects between `saturating_add` (correct) and `wrapping_add` (legacy) for all four relative-timelock comparisons. When `nowrap=false`, an attacker-controlled CLVM condition value that causes integer overflow wraps around to a small number, silently inverting the timelock check. This is the direct analog of the external report's "implicit restriction" class: the relative-timelock field has an implicit maximum (`u32::MAX - confirmed_block_index` for height, `u64::MAX - timestamp` for seconds), and values exceeding that maximum produce incorrect enforcement rather than a clean error.

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` evaluates four relative-timelock conditions per spend. For each, it branches on `nowrap`:

```
// ASSERT_HEIGHT_RELATIVE
if nowrap {
    prev_height < confirmed.saturating_add(height_relative)   // correct
} else {
    prev_height < confirmed.wrapping_add(height_relative)     // wraps on overflow
}
```

The same pattern applies to `seconds_relative`, `before_height_relative`, and `before_seconds_relative`. [1](#0-0) 

The condition parser (`conditions.rs`) accepts any `height_relative` value up to `u32::MAX` (4 bytes, no positive-overflow rejection): [2](#0-1) 

So a CLVM puzzle can legally emit `ASSERT_HEIGHT_RELATIVE 0xFFFFFFFF`. Under `nowrap=false`:

- `confirmed_block_index = 100`, `height_relative = 0xFFFF_FFFF`
- `100u32.wrapping_add(0xFFFF_FFFF)` = `99`
- Check: `prev_height < 99` — always `false` once the coin is confirmed, so the spend **passes immediately** instead of being locked for ~4 billion blocks.

The tests explicitly document and confirm this divergence: [3](#0-2) 

The inverse failure occurs for `ASSERT_BEFORE_HEIGHT_RELATIVE`: wrapping to a small value makes `prev_height >= 0` always true, permanently rejecting a spend that should be valid: [4](#0-3) 

The Python binding exposes `nowrap` as a caller-controlled parameter: [5](#0-4) 

### Impact Explanation

Two distinct impacts arise:

1. **Timelock bypass (High):** A coin whose puzzle enforces `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` with a value that overflows `u32`/`u64` when added to the coin's confirmation height/timestamp is spendable immediately under `nowrap=false`. The intended lock period (potentially billions of blocks/seconds) is silently discarded.

2. **Consensus divergence (Critical):** If any production full-node path calls `check_time_locks` with `nowrap=false` while another calls it with `nowrap=true`, the two nodes reach opposite accept/reject decisions for the same spend bundle containing an overflow-inducing relative timelock. This is a deterministic chain split triggered by a single unprivileged CLVM condition value.

### Likelihood Explanation

The `nowrap=false` path is the legacy behavior retained for backward compatibility. The Python binding accepts `nowrap` as a runtime argument, meaning the full-node Python layer controls which path is taken. If any consensus-critical call site passes `nowrap=false` (e.g., during block validation of pre-fork blocks, or due to a misconfigured flag), the wrapping path is live. The attacker entry point is a standard CLVM puzzle emitting `ASSERT_HEIGHT_RELATIVE` with a near-`u32::MAX` value — no special privileges required.

### Recommendation

- Remove the `nowrap=false` / `wrapping_add` path entirely from `check_time_locks` once the soft-fork activating saturating semantics is fully enforced. Until then, document precisely which block heights require `nowrap=false` and enforce that the flag cannot be passed incorrectly from the Python layer.
- Alternatively, add a pre-check in the condition parser: reject `height_relative` values that would overflow when added to any plausible `confirmed_block_index`, or clamp them to `u32::MAX` via saturating semantics unconditionally.

### Proof of Concept

Craft a CLVM puzzle that outputs:

```
(ASSERT_HEIGHT_RELATIVE . 0xFFFFFFFF)
```

Spend the coin at any block height after confirmation. Under `nowrap=false`:

```
confirmed = 100
100u32.wrapping_add(0xFFFF_FFFF) = 99
prev_height (e.g. 200) < 99  →  false  →  Ok(())   ← timelock bypassed
```

Under `nowrap=true`:

```
100u32.saturating_add(0xFFFF_FFFF) = u32::MAX
prev_height (200) < u32::MAX  →  true  →  Err(AssertHeightRelativeFailed)  ← correctly rejected
```

The same spend bundle is accepted by one node class and rejected by the other, producing consensus divergence. [1](#0-0) [6](#0-5)

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
