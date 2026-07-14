### Title
Integer Wrapping in Relative Timelock Arithmetic Enables Timelock Bypass — (`File: crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

`check_time_locks` contains two arithmetic paths for relative timelock evaluation, controlled by a `nowrap: bool` parameter. When `nowrap=false`, the function uses `wrapping_add` for both `ASSERT_HEIGHT_RELATIVE`/`ASSERT_SECONDS_RELATIVE` and their `ASSERT_BEFORE_*` counterparts. A coin with a crafted large relative-lock value (e.g., `height_relative = 0xFFFF_FFFF`) causes the addition to wrap around to a small integer, making the timelock check pass immediately — bypassing the intended lock entirely.

---

### Finding Description

In `check_time_locks`, the relative height and seconds checks branch on `nowrap`:

```rust
// crates/chia-consensus/src/check_time_locks.rs, lines 55–68
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

The same pattern applies to `seconds_relative` (lines 70–78), `before_height_relative` (lines 79–98), and `before_seconds_relative` (lines 100–112). [2](#0-1) 

When `nowrap=false`:

- `confirmed_block_index = 10`, `height_relative = 0xFFFF_FFFF`:
  `10u32.wrapping_add(0xFFFF_FFFF) = 9`
  → `prev_height=15 < 9` is **false** → **no error** → spend accepted despite a ~4-billion-block lock.

- `confirmed_block_index = 10`, `before_height_relative = 0xFFFF_FFFF`:
  `10u32.wrapping_add(0xFFFF_FFFF) = 9`
  → `prev_height=15 >= 9` is **true** → **error** → spend rejected despite the before-lock being far in the future.

The Python-exposed binding `py_check_time_locks` passes `nowrap` directly from the caller: [3](#0-2) 

The public Python API signature confirms `nowrap: bool` is a caller-controlled parameter: [4](#0-3) 

The test suite explicitly documents the divergence between the two paths: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

**High — Timelock validation bypass enables unauthorized spend acceptance.**

When `nowrap=false` is active (the pre-soft-fork code path), any coin whose puzzle emits `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` with a value large enough to cause `u32`/`u64` wrapping when added to `confirmed_block_index`/`timestamp` will have its timelock silently bypassed. The spend is accepted as valid at any block height ≥ the wrapped (small) result. This allows premature spending of coins that are supposed to be locked for billions of blocks or seconds — enabling theft from vaults, escrows, or any protocol relying on relative timelocks for security.

The inverse also holds for `ASSERT_BEFORE_HEIGHT_RELATIVE`/`ASSERT_BEFORE_SECONDS_RELATIVE`: wrapping causes a spend to be **incorrectly rejected**, which can freeze funds permanently.

---

### Likelihood Explanation

The `nowrap=false` path is the legacy behavior retained for backward compatibility during a soft-fork transition. Any node or Python caller that invokes `check_time_locks(..., nowrap=False)` for blocks before the fork activation height is vulnerable. An attacker who knows the activation height can craft a coin before activation with a carefully chosen `height_relative` or `seconds_relative` value (e.g., `0xFFFF_FFFF - confirmed_block_index + 1`) that wraps to a small number, then spend it immediately on nodes still using the wrapping path. The attacker controls the puzzle and therefore the condition values entirely.

---

### Recommendation

1. Remove the `nowrap=false` / `wrapping_add` code path entirely once the soft fork has activated on all supported networks. The `saturating_add` path is the correct behavior.
2. Until removal, add a hard assertion or compile-time gate that prevents `nowrap=false` from being used in any consensus-critical call site after the fork activation height.
3. Audit all Python call sites in chia-blockchain that invoke `check_time_locks` to confirm `nowrap=True` is always passed for any block at or above the fork activation height.

---

### Proof of Concept

Craft a coin with `ASSERT_HEIGHT_RELATIVE = 0xFFFF_FFFF` confirmed at block index 10. Call `check_time_locks` with `nowrap=False` at `prev_transaction_block_height=15`:

```
confirmed_block_index = 10
height_relative       = 0xFFFF_FFFF   # intended: ~4.3 billion block lock

# wrapping path (nowrap=False):
threshold = 10u32.wrapping_add(0xFFFF_FFFF) = 9

# check: 15 < 9  →  False  →  no error  →  spend ACCEPTED
```

With `nowrap=True` (saturating):
```
threshold = 10u32.saturating_add(0xFFFF_FFFF) = u32::MAX = 4294967295

# check: 15 < 4294967295  →  True  →  AssertHeightRelativeFailed  →  spend REJECTED
```

This is confirmed by the existing test vectors: [7](#0-6)

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

**File:** crates/chia-consensus/src/check_time_locks.rs (L70-112)
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

**File:** crates/chia-consensus/src/check_time_locks.rs (L276-282)
```rust
    // 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
    #[case::height_relative_wrap(
        Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
        Ok(()),
    )]
    // seconds_relative check: timestamp < coin_time + seconds_relative -> Err
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
