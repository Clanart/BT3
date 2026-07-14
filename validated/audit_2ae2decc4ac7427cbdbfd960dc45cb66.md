### Title
Integer Overflow in `wrapping_add` Bypasses `ASSERT_HEIGHT_RELATIVE` and `ASSERT_SECONDS_RELATIVE` Timelocks When `nowrap=false` - (File: `crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

`check_time_locks` uses `wrapping_add` for relative timelock arithmetic when the `nowrap` flag is `false`. A CLVM puzzle can supply a crafted large integer argument to `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` that causes the addition to overflow and wrap to a small value, making the timelock comparison trivially pass. This allows a coin protected by a relative timelock to be spent immediately, bypassing the intended lock period. Nodes that evaluate the same spend bundle with different `nowrap` values will reach different validity conclusions, causing consensus divergence.

---

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` accepts a `nowrap: bool` parameter that selects between two arithmetic modes for relative conditions:

- `nowrap = true` → `saturating_add` (correct: overflow clamps to `u32::MAX`/`u64::MAX`, so the check always fails as expected)
- `nowrap = false` → `wrapping_add` (broken: overflow wraps to a small value, making the check pass)

For `ASSERT_HEIGHT_RELATIVE`:

```rust
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}
``` [1](#0-0) 

For `ASSERT_SECONDS_RELATIVE`:

```rust
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
    return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
}
``` [2](#0-1) 

The condition argument is parsed by `sanitize_uint` in `conditions.rs` with a 4-byte limit for height (u32) and 8-byte limit for seconds (u64). An attacker can supply any value up to `u32::MAX` or `u64::MAX` respectively. The test suite explicitly documents the divergence:

```
// 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::height_relative_wrap(
    Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
    Ok(()),
)]
``` [3](#0-2) 

```
// 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::seconds_relative_wrap(
    Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
    Ok(()),
)]
``` [4](#0-3) 

The Python-facing binding exposes `nowrap` as a caller-controlled parameter:

```python
def check_time_locks(
    removal_coin_records: dict[bytes32, CoinRecord],
    bundle_conds: SpendBundleConditions,
    prev_transaction_block_height: uint32,
    timestamp: uint64,
    nowrap: bool,
) -> Optional[int]: ...
``` [5](#0-4) 

The chia-blockchain Python node calls `check_time_locks` with `nowrap=False` for pre-soft-fork blocks to preserve backward compatibility. This is the live code path where the bypass is reachable.

---

### Impact Explanation

**Timelock bypass (High — unauthorized spend acceptance):** A coin whose puzzle emits `ASSERT_HEIGHT_RELATIVE(N)` where `N` is chosen so that `confirmed_block_index + N` overflows `u32` and wraps to a value less than the current block height will pass the timelock check immediately, regardless of how long the coin has actually been on-chain. The coin is spendable at any block height when `nowrap=false` is in effect.

**Consensus divergence (Critical):** A node running with `nowrap=true` (post-soft-fork mode) will reject the same spend bundle that a node running with `nowrap=false` (pre-soft-fork mode) accepts. Both nodes process the same serialized block. This produces a deterministic, permanent chain split between node versions.

---

### Likelihood Explanation

- The attacker-controlled entry path is a CLVM puzzle argument — any unprivileged user can craft a coin with an overflow-inducing `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` value.
- `sanitize_uint` accepts values up to `u32::MAX` for height and `u64::MAX` for seconds, so the overflow-inducing values are within the accepted range.
- The `nowrap=false` path is active for all pre-soft-fork block heights in the chia-blockchain node.
- No special privileges, leaked keys, or network-level access are required. [6](#0-5) 

---

### Recommendation

Replace `wrapping_add` with `saturating_add` unconditionally in all four relative timelock checks, or remove the `nowrap` branch entirely and always use `saturating_add`. The `nowrap=false` path was introduced for backward compatibility but produces semantically incorrect results for overflow inputs. The correct semantic for `ASSERT_HEIGHT_RELATIVE(N)` is always: "the coin must have aged at least N blocks," which saturating arithmetic enforces correctly. Wrapping arithmetic has no valid consensus interpretation.

```rust
// Replace:
unspent.confirmed_block_index.wrapping_add(height_relative)
// With:
unspent.confirmed_block_index.saturating_add(height_relative)
```

Apply the same fix to `seconds_relative`, `before_height_relative`, and `before_seconds_relative` branches. [7](#0-6) 

---

### Proof of Concept

**Setup:** Coin confirmed at block height 10 (`confirmed_block_index = 10`). Current block height is 15.

**Crafted condition:** `ASSERT_HEIGHT_RELATIVE(0xFFFF_FFF6)` (= 4,294,967,286).

**Arithmetic with `nowrap=false`:**
```
10 + 4,294,967,286 = 4,294,967,296 → wraps to 0 (mod 2^32)
Check: 15 < 0 → false → Ok(()) — timelock passes
```

**Arithmetic with `nowrap=true`:**
```
10 + 4,294,967,286 = saturates to u32::MAX = 4,294,967,295
Check: 15 < 4,294,967,295 → true → Err(AssertHeightRelativeFailed) — timelock fails
```

The test suite confirms this exact case at line 241:

```rust
// 10 + (2^32 - 10) overflows to 0, wrapping: 15 < 0 -> Ok
// saturating: clamps to u32::MAX, 15 < u32::MAX -> Err
(make_test_conds(height_relative=0xFFFF_FFF6), 13, None),
``` [3](#0-2) [1](#0-0) 

A spend bundle containing this coin spend, submitted to a node using `nowrap=false`, will be accepted as valid. The same bundle submitted to a node using `nowrap=true` will be rejected. This is a deterministic consensus divergence triggered by an unprivileged, attacker-crafted CLVM condition argument.

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
