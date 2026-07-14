### Title
Integer Overflow in Relative Timelock Arithmetic Enables Timelock Bypass — (`File: crates/chia-consensus/src/check_time_locks.rs`)

### Summary
`check_time_locks` uses `wrapping_add` (when `nowrap=false`) for `ASSERT_HEIGHT_RELATIVE`, `ASSERT_SECONDS_RELATIVE`, `ASSERT_BEFORE_HEIGHT_RELATIVE`, and `ASSERT_BEFORE_SECONDS_RELATIVE` condition checks. An attacker can craft a `height_relative` (u32) or `seconds_relative` (u64) value that causes the sum `confirmed_block_index + height_relative` to wrap around to a small integer, making the timelock check pass immediately — bypassing the intended spend restriction.

### Finding Description

In `check_time_locks`, when `nowrap=false`, the relative timelock comparisons use Rust's `wrapping_add`:

```rust
// ASSERT_HEIGHT_RELATIVE — nowrap=false branch
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

The same wrapping pattern applies to `seconds_relative` and `before_seconds_relative` (u64): [3](#0-2) [4](#0-3) 

The `nowrap` parameter is exposed directly through the Python binding: [5](#0-4) 

The `height_relative` field is a `u32` parsed from a CLVM condition argument via `sanitize_uint` with `max_size=4`, meaning any value up to `u32::MAX` (0xFFFFFFFF) is accepted as valid: [6](#0-5) 

**Concrete overflow scenario (ASSERT_HEIGHT_RELATIVE):**

- Coin confirmed at `confirmed_block_index = 100`
- Attacker sets `height_relative = u32::MAX - 99 = 4_294_967_196`
- `100u32.wrapping_add(4_294_967_196) = 0`
- Check: `prev_height < 0` — always `false` for any u32 `prev_height`
- Result: the check never fires; the coin is spendable **immediately** at any height

The existing test suite explicitly documents this divergence: [7](#0-6) 

The Python-level test confirms the same wrapping bypass for both height and seconds variants: [8](#0-7) 

### Impact Explanation

When `nowrap=false` is active in the production call path, any coin protected by `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` with a carefully chosen large value can be spent **before** its intended unlock height/time. This is a **timelock validation bypass enabling unauthorized spend acceptance** — matching the High impact category. The bypass is deterministic and reproducible across all nodes running the same `nowrap=false` path, so it does not cause consensus divergence between nodes using the same flag value, but it does allow coins to be stolen/spent prematurely.

### Likelihood Explanation

- The `nowrap` parameter is a caller-controlled boolean passed through the Python binding. If any production code path passes `nowrap=False`, the vulnerability is reachable.
- The attacker-controlled entry point is a standard CLVM spend bundle containing an `ASSERT_HEIGHT_RELATIVE` condition with a crafted large integer — fully within the accepted range (≤ u32::MAX) and parseable by `sanitize_uint`.
- No privileged role, leaked key, or network-level attack is required.

### Recommendation

Replace `wrapping_add` with `saturating_add` unconditionally for all relative timelock arithmetic, removing the `nowrap=false` code path entirely. The `nowrap=true` (saturating) branch already implements the correct behavior:

```rust
// Correct — always use saturating_add
if prev_transaction_block_height
    < unspent.confirmed_block_index.saturating_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}
``` [9](#0-8) 

The `nowrap` parameter and all `wrapping_add` branches should be removed from `check_time_locks` and its Python binding.

### Proof of Concept

**Setup:** Coin confirmed at block height 100. Attacker constructs a spend bundle with:
- `ASSERT_HEIGHT_RELATIVE = 4_294_967_196` (= `u32::MAX - 99`)

**Wrapping arithmetic (nowrap=false):**
```
confirmed_block_index.wrapping_add(height_relative)
= 100u32.wrapping_add(4_294_967_196)
= (100 + 4_294_967_196) mod 2^32
= 4_294_967_296 mod 2^32
= 0
```

**Check:** `prev_transaction_block_height < 0` → always `false` for any u32 value.

**Result:** `AssertHeightRelativeFailed` is never returned. The coin is spendable at **any** block height, bypassing the intended `u32::MAX - 99` block lock. The same construction applies to `seconds_relative` using u64 arithmetic. [10](#0-9)

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

**File:** crates/chia-consensus/src/conditions.rs (L630-644)
```rust
        ASSERT_HEIGHT_ABSOLUTE => {
            maybe_check_args_terminator(a, c, flags)?;
            let node = first(a, c)?;
            match sanitize_uint(
                a,
                node,
                4,
                ValidationErr::Err(ErrorCode::AssertHeightAbsoluteFailed),
            )? {
                SanitizedUint::PositiveOverflow => {
                    Err(ValidationErr::Err(ErrorCode::AssertHeightAbsoluteFailed))
                }
                SanitizedUint::NegativeOverflow => Ok(Condition::Skip),
                SanitizedUint::Ok(r) => Ok(Condition::AssertHeightAbsolute(r as u32)),
            }
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
