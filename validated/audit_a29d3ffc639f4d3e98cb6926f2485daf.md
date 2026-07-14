### Title
Integer Overflow in `wrapping_add` Arithmetic Bypasses `ASSERT_SECONDS_RELATIVE` / `ASSERT_HEIGHT_RELATIVE` Timelocks When `nowrap=false` - (File: `crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

`check_time_locks` contains two distinct arithmetic paths controlled by the `nowrap: bool` parameter. When `nowrap=false` (the legacy pre-hard-fork path), relative timelock conditions use `wrapping_add`, which silently overflows for large attacker-supplied values. This causes the timelock deadline to wrap around to a value smaller than the current timestamp, making the guard check evaluate to `false` and **passing the spend as valid** even though the timelock has not elapsed. The inverse overflow on `ASSERT_BEFORE_*_RELATIVE` conditions causes a false rejection of valid spends.

---

### Finding Description

In `check_time_locks`, the `ASSERT_SECONDS_RELATIVE` guard is:

```rust
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
    return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
}
```

When `nowrap=false`, `wrapping_add` is used. If an attacker crafts a CLVM puzzle that emits `ASSERT_SECONDS_RELATIVE(N)` where `N` is chosen so that `coin_timestamp + N` overflows `u64`, the computed deadline wraps to a value smaller than the current block timestamp. The comparison `timestamp < wrapped_deadline` then evaluates to `false`, so no error is returned and the spend is accepted — even though the intended timelock has not expired.

Concrete example (from the codebase's own test documentation):
- `coin_timestamp = 1000`, `seconds_relative = u64::MAX`
- `wrapping_add(1000, u64::MAX) = 999`
- Check: `2000 < 999` → `false` → **no error, spend accepted**

The `nowrap=true` path correctly uses `saturating_add`, which clamps to `u64::MAX`, making the check `2000 < u64::MAX` → `true` → error returned (timelock enforced).

The same overflow affects `ASSERT_HEIGHT_RELATIVE` (u32 wrapping): [1](#0-0) 

And the inverse (false rejection) affects `ASSERT_BEFORE_HEIGHT_RELATIVE` and `ASSERT_BEFORE_SECONDS_RELATIVE`: [2](#0-1) 

The two arithmetic paths are explicitly branched on `nowrap`: [3](#0-2) 

The `nowrap` parameter is a caller-controlled boolean exposed directly through the Python binding: [4](#0-3) [5](#0-4) 

The Python-facing stub confirms `nowrap` is an explicit, caller-supplied argument with no enforcement of which value is correct for a given block height: [6](#0-5) 

The codebase's own unit tests document the divergent outcomes explicitly: [7](#0-6) 

---

### Impact Explanation

When `nowrap=false` is passed by the Python full node for pre-hard-fork blocks, an attacker who controls a coin's puzzle can emit `ASSERT_SECONDS_RELATIVE` with a value engineered to overflow `u64` when added to the coin's creation timestamp. The wrapping arithmetic produces a deadline in the past, causing the timelock guard to pass unconditionally. The coin is accepted as spendable before its intended lock period expires.

This matches the allowed High impact: **timelock condition validation bypass enables unauthorized spend acceptance**.

The symmetric overflow on `ASSERT_BEFORE_SECONDS_RELATIVE` / `ASSERT_BEFORE_HEIGHT_RELATIVE` causes valid spends to be incorrectly rejected, which can cause consensus divergence between nodes running different versions or different `nowrap` settings.

---

### Likelihood Explanation

- The `nowrap=false` path is explicitly present, tested, and exposed as a Python API parameter with no enforcement of which value is correct for a given block height.
- The Python full node (chia-blockchain) is responsible for passing the correct `nowrap` value; if it passes `nowrap=False` for any block height where `ASSERT_SECONDS_RELATIVE` / `ASSERT_HEIGHT_RELATIVE` conditions are consensus-valid, the bypass is reachable.
- An attacker only needs to craft a CLVM puzzle that outputs a large relative timelock value — this is entirely within unprivileged spend input.
- The overflow values are deterministic and computable from the coin's known creation timestamp.

---

### Recommendation

1. Remove the `nowrap=false` / `wrapping_add` path entirely from `check_time_locks`. The `saturating_add` behavior is the correct semantic for all timelock arithmetic: a timelock value that overflows should be treated as effectively infinite (never satisfied for `ASSERT_*_RELATIVE`) or effectively zero (always satisfied for `ASSERT_BEFORE_*_RELATIVE`).
2. If backward compatibility with pre-hard-fork blocks requires the wrapping behavior, document precisely which block heights use `nowrap=false` and enforce this mapping inside `check_time_locks` itself (derived from `prev_transaction_block_height` and consensus constants), rather than leaving it as an unchecked caller-supplied boolean.
3. Audit all Python call sites of `check_time_locks` to confirm `nowrap=True` is passed for every block height where relative timelock conditions are active.

---

### Proof of Concept

The codebase's own test suite documents the bypass directly:

```
// 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::seconds_relative_wrap(
    Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),  // nowrap=true: enforced
    Ok(()),  // nowrap=false: BYPASSED
)]
``` [7](#0-6) 

Attacker steps:
1. Create a coin whose puzzle outputs `(ASSERT_SECONDS_RELATIVE . <u64::MAX - coin_creation_timestamp + 1>)`.
2. Submit a spend bundle for this coin to a node validating with `nowrap=false`.
3. `check_time_locks` computes `coin_timestamp.wrapping_add(seconds_relative)` → wraps to a value less than the current timestamp.
4. The guard `timestamp < wrapped_deadline` is `false` → no error → spend accepted.
5. The coin is spent before its intended timelock expires.

### Citations

**File:** crates/chia-consensus/src/check_time_locks.rs (L12-18)
```rust
pub fn check_time_locks(
    removal_coin_records: &HashMap<Bytes32, CoinRecord>,
    bundle_conds: &OwnedSpendBundleConditions,
    prev_transaction_block_height: u32,
    timestamp: u64,
    nowrap: bool,
) -> Result<(), ValidationErr> {
```

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

**File:** crates/chia-consensus/src/check_time_locks.rs (L100-111)
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
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L122-128)
```rust
pub fn py_check_time_locks(
    removal_coin_records: HashMap<Bytes32, CoinRecord>,
    bundle_conds: &OwnedSpendBundleConditions,
    prev_transaction_block_height: u32,
    timestamp: u64,
    nowrap: bool,
) -> PyResult<Option<u32>> {
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
