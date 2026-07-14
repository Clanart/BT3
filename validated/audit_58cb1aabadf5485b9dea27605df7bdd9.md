### Title
Integer Overflow in `check_time_locks` Bypasses `ASSERT_SECONDS_RELATIVE` / `ASSERT_HEIGHT_RELATIVE` Timelocks When `nowrap=false` — (`File: crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

When `check_time_locks` is called with `nowrap=false` (the legacy pre-softfork code path), the relative timelock checks for `ASSERT_SECONDS_RELATIVE` and `ASSERT_HEIGHT_RELATIVE` use `wrapping_add` instead of `saturating_add`. A CLVM puzzle that emits either condition with a near-maximum integer value causes the deadline to wrap around to a value smaller than the coin's birth time, making the comparison trivially pass. A coin that should be locked for an astronomically long period can be spent immediately.

---

### Finding Description

`check_time_locks` enforces per-spend relative timelocks by computing a deadline and comparing it against the current block height or timestamp. The function accepts a `nowrap: bool` parameter that selects between two arithmetic modes: [1](#0-0) 

When `nowrap=true`, `saturating_add` is used: any overflow clamps to the type maximum, so the deadline is always ≥ the coin's birth value, and the check correctly fails. When `nowrap=false`, `wrapping_add` is used: overflow wraps modulo 2^N, producing a deadline that is *smaller* than the coin's birth value.

Concretely, for `ASSERT_SECONDS_RELATIVE`:

```
coin_timestamp = T
seconds_relative = u64::MAX

deadline = T.wrapping_add(u64::MAX) = T - 1   (wraps)
check:  current_timestamp < (T - 1)  →  False  →  PASSES
```

The same applies to `ASSERT_HEIGHT_RELATIVE` with `height_relative = u32::MAX`:

```
confirmed_block_index = H
height_relative = u32::MAX

deadline = H.wrapping_add(u32::MAX) = H - 1   (wraps)
check:  prev_height < (H - 1)  →  False  →  PASSES
```

The test suite explicitly documents and accepts this divergence: [2](#0-1) 

The Python-level wrapping test confirms the same behavior end-to-end: [3](#0-2) 

The `nowrap` parameter is part of the public Python API: [4](#0-3) 

The condition parser accepts `seconds_relative` up to `u64::MAX` and `height_relative` up to `u32::MAX` as valid values (positive-overflow is rejected, but the maximum representable value is not): [5](#0-4) 

---

### Impact Explanation

This is a **timelock condition validation bypass enabling unauthorized spend acceptance** (High impact per scope). A coin whose puzzle emits `ASSERT_SECONDS_RELATIVE = 0xFFFF_FFFF_FFFF_FFFF` is intended to be unspendable for ~584 billion years. Under `nowrap=false`, the deadline wraps to `coin_timestamp - 1`, which is always already in the past, so the coin can be spent in the very next block. Any coin value locked behind such a puzzle is immediately accessible to the puzzle's owner, defeating the timelock entirely.

---

### Likelihood Explanation

The `nowrap=false` path is the legacy behavior used for blocks before the softfork activation height. Any node that calls `check_time_locks(..., nowrap=false)` for a block containing a spend with a near-maximum relative timelock value will accept the spend. An attacker who knows the softfork activation height can deliberately target the pre-activation window. The attacker-controlled entry is a valid CLVM puzzle submitted in a spend bundle — no privileged access is required.

---

### Recommendation

Replace `wrapping_add` with `saturating_add` unconditionally in both the `height_relative` and `seconds_relative` branches, removing the `nowrap` bifurcation for these checks. Alternatively, if the legacy `nowrap=false` path must be preserved for historical block replay, document that it must never be used for mempool or new-block validation, and add a hard assertion or type-level enforcement to prevent callers from passing `nowrap=false` in those contexts.

---

### Proof of Concept

Craft a CLVM puzzle that outputs the condition `(ASSERT_SECONDS_RELATIVE . 0xFFFF_FFFF_FFFF_FFFF)`. Submit a spend bundle containing a coin locked by this puzzle. Call `check_time_locks` with `nowrap=false` at any block timestamp. The deadline computation wraps to `coin_timestamp - 1`; the check `timestamp < coin_timestamp - 1` is false; the function returns `Ok(())` and the spend is accepted — despite the coin being intended to be locked for ~584 billion years.

The test case at line 302–306 of `check_time_locks.rs` already proves this:

```
seconds_relative = u64::MAX
nowrap=true  → Err(AssertSecondsRelativeFailed)   ← correct
nowrap=false → Ok(())                              ← bypass
``` [6](#0-5)

### Citations

**File:** crates/chia-consensus/src/check_time_locks.rs (L55-78)
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

**File:** tests/test_check_time_locks.py (L239-251)
```python
            # 10 + (2^32 - 10) overflows to 0, wrapping: 15 < 0 -> Ok
            # saturating: clamps to u32::MAX, 15 < u32::MAX -> Err
            (make_test_conds(height_relative=0xFFFF_FFF6), 13, None),
            # 10 + u32::MAX overflows to 9, wrapping: 15 < 9 -> Ok
            (make_test_conds(height_relative=0xFFFF_FFFF), 13, None),
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

**File:** crates/chia-consensus/src/conditions.rs (L582-628)
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
        }
        ASSERT_SECONDS_ABSOLUTE => {
            maybe_check_args_terminator(a, c, flags)?;
            let node = first(a, c)?;
            match sanitize_uint(
                a,
                node,
                8,
                ValidationErr::Err(ErrorCode::AssertSecondsAbsoluteFailed),
            )? {
                SanitizedUint::PositiveOverflow => {
                    Err(ValidationErr::Err(ErrorCode::AssertSecondsAbsoluteFailed))
                }
                SanitizedUint::NegativeOverflow => Ok(Condition::Skip),
                SanitizedUint::Ok(r) => Ok(Condition::AssertSecondsAbsolute(r)),
            }
        }
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
