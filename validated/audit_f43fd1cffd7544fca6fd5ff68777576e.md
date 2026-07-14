### Title
Integer Wrapping in Relative Timelock Validation Enables Timelock Bypass — (File: `crates/chia-consensus/src/check_time_locks.rs`)

### Summary
When `check_time_locks` is called with `nowrap=false`, relative timelock conditions (`ASSERT_HEIGHT_RELATIVE`, `ASSERT_SECONDS_RELATIVE`) are evaluated using `wrapping_add` instead of `saturating_add`. A crafted spend bundle supplying a large relative timelock value causes the sum to wrap around to a small integer, making the timelock check trivially pass and allowing the coin to be spent before the required height or timestamp.

### Finding Description
In `check_time_locks`, the `nowrap` boolean selects between two arithmetic paths for relative timelock addition:

- `nowrap=true` → `saturating_add` (correct: clamps at `u32::MAX` / `u64::MAX`)
- `nowrap=false` → `wrapping_add` (incorrect: silently wraps around)

For `ASSERT_HEIGHT_RELATIVE` with `nowrap=false`:

```rust
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}
```

If `confirmed_block_index = 10` and `height_relative = 0xFFFF_FFF6`, then `wrapping_add` yields `0`. The check `prev_height < 0` is always `false`, so the function returns `Ok(())` — the timelock is bypassed entirely. [1](#0-0) 

The same wrapping flaw applies to `ASSERT_SECONDS_RELATIVE`: [2](#0-1) 

The inverse problem (false rejection) affects `ASSERT_BEFORE_HEIGHT_RELATIVE` and `ASSERT_BEFORE_SECONDS_RELATIVE`: wrapping to a small value makes `prev_height >= wrapped_sum` trivially true, causing a valid spend to be incorrectly rejected. [3](#0-2) 

The Python binding exposes the `nowrap` parameter directly to callers, meaning both paths are reachable from production code: [4](#0-3) 

The test suite explicitly documents and confirms the wrapping bypass behavior: [5](#0-4) 

### Impact Explanation
When `nowrap=false` is active (e.g., for pre-hardFork blocks or any caller that passes `False`), an attacker can craft a coin spend with `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` set to a value large enough to cause `wrapping_add` to produce a result smaller than the current block height or timestamp. The timelock check passes unconditionally, allowing the coin to be spent before the intended lock expires. This is a **timelock condition validation bypass enabling unauthorized spend acceptance**, matching the High impact category.

### Likelihood Explanation
The `nowrap` parameter is caller-controlled via the Python binding. Any node or mempool validator that invokes `check_time_locks` with `nowrap=False` is vulnerable. The wrapping threshold is deterministic and computable: for a coin confirmed at height `h`, setting `height_relative = (u32::MAX - h + 1)` causes the wrap. An attacker who can create a coin and control the `ASSERT_HEIGHT_RELATIVE` argument in the puzzle solution can trigger this precisely.

### Recommendation
Remove the `wrapping_add` path entirely. The `nowrap=false` branch should either be deleted (if no longer needed for any consensus-valid block range) or replaced with `saturating_add` to match the `nowrap=true` path. If backward compatibility with pre-fork blocks is required, the saturating behavior is still semantically correct for those blocks (a saturating overflow means the timelock can never be satisfied, which is the safe/conservative outcome). The `nowrap` parameter itself should be deprecated and removed.

### Proof of Concept
From the test suite (confirmed behavior):

```python
# coin confirmed at height 10, prev_height=15
# height_relative=0xFFFF_FFF6: 10 + 0xFFFF_FFF6 wraps to 0
# check: 15 < 0 → False → Ok (timelock bypassed)
make_test_conds(height_relative=0xFFFF_FFF6)
# nowrap=False → returns None (no error) ← BYPASS
# nowrap=True  → returns 13 (ASSERT_HEIGHT_RELATIVE_FAILED) ← correct

# coin timestamp=10000, prev_timestamp=10150
# seconds_relative=0xFFFF_FFFF_FFFF_D8F0: 10000 + value wraps to 0
# check: 10150 < 0 → False → Ok (timelock bypassed)
make_test_conds(seconds_relative=0xFFFF_FFFF_FFFF_D8F0)
# nowrap=False → returns None (no error) ← BYPASS
# nowrap=True  → returns 105 (ASSERT_SECONDS_RELATIVE_FAILED) ← correct
``` [5](#0-4)

### Citations

**File:** crates/chia-consensus/src/check_time_locks.rs (L64-68)
```rust
            } else if prev_transaction_block_height
                < unspent.confirmed_block_index.wrapping_add(height_relative)
            {
                return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
            }
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L75-77)
```rust
            } else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
                return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
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

**File:** tests/test_check_time_locks.py (L238-251)
```python
            (make_test_conds(height_relative=0xFFFF_FFF5), 13, 13),
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
