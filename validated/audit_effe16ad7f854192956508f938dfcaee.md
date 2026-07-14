### Title
Wrapping Integer Overflow in Relative Timelock Arithmetic Enables Timelock Bypass and Consensus Divergence - (File: `crates/chia-consensus/src/check_time_locks.rs`)

### Summary
`check_time_locks` uses `wrapping_add` when the `nowrap` flag is `false`, causing `coin_time + seconds_relative` (and the height equivalent) to silently wrap around to a small value when the relative argument is near `u64::MAX` / `u32::MAX`. This makes an `ASSERT_SECONDS_RELATIVE` or `ASSERT_HEIGHT_RELATIVE` condition that should never be satisfiable pass immediately, bypassing the timelock. Simultaneously, nodes running with `nowrap=true` (saturating arithmetic) reject the same spend, producing deterministic consensus divergence between the two node populations.

### Finding Description

`check_time_locks` accepts a `nowrap: bool` parameter that selects between two arithmetic modes for relative timelock evaluation:

- `nowrap=true` → `saturating_add` (correct: clamps to `u64::MAX`)
- `nowrap=false` → `wrapping_add` (legacy: silently wraps to a small value) [1](#0-0) 

For `ASSERT_SECONDS_RELATIVE`, the check is:

```
if timestamp < coin_time.wrapping_add(seconds_relative) { Err }
```

When `seconds_relative = u64::MAX` and `coin_time = T`, `wrapping_add` produces `T - 1`. Since any real `timestamp ≥ T`, the condition `timestamp < T - 1` is false, so the check **passes** (no error). The coin is accepted as if the timelock were already satisfied, even though the intended lock is `T + u64::MAX` seconds — effectively never.

The same overflow applies to `ASSERT_HEIGHT_RELATIVE` using `u32::wrapping_add`. [2](#0-1) 

The inverse condition `ASSERT_BEFORE_SECONDS_RELATIVE` is also affected: wrapping produces a small deadline, causing the check `timestamp >= small_value` to **fail** (false rejection) when it should pass. [3](#0-2) 

The test suite explicitly documents and confirms this divergence: [4](#0-3) 

```
// 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
seconds_relative = 0xffff_ffff_ffff_ffff
nowrap=true  → Err (correctly blocked)
nowrap=false → Ok  (timelock bypassed)
```

The Python binding exposes `nowrap` as a caller-controlled parameter: [5](#0-4) 

### Impact Explanation

**Timelock bypass (High):** A coin whose puzzle includes `(ASSERT_SECONDS_RELATIVE 0xFFFFFFFFFFFFFFFF)` can be spent immediately on any node running with `nowrap=false`. The relative lock — intended to require ~584 billion years — wraps to `coin_time - 1`, which is already satisfied at the moment of coin creation.

**Consensus divergence (Critical):** A farmer includes such a spend in a block. Nodes with `nowrap=false` accept the block; nodes with `nowrap=true` reject it with `AssertSecondsRelativeFailed`. The two populations follow different chains, producing a deterministic chain split.

Both impacts fall within the allowed scope:
- *High*: timelock condition validation bypass enables unauthorized spend acceptance.
- *Critical*: valid unprivileged spend bundle triggers deterministic consensus divergence.

### Likelihood Explanation

The `nowrap=false` path is reachable by any unprivileged spend bundle. The attacker only needs to craft a coin whose puzzle outputs `(ASSERT_SECONDS_RELATIVE <near-max-u64>)` and submit a spend. No privileged role, key leak, or network-level attack is required. The condition argument is attacker-controlled CLVM output, parsed through `sanitize_uint` which accepts any 8-byte value up to `u64::MAX`: [6](#0-5) 

The only prerequisite is that the full node calls `check_time_locks` with `nowrap=false`. Whether this is the case in production depends on the Python caller, which is outside the Rust scope but the binding makes it trivially passable.

### Recommendation

1. **Remove the `nowrap=false` / `wrapping_add` path entirely** once the soft-fork activation height is finalized. The wrapping behavior is semantically incorrect for all relative timelock conditions.
2. Until removal, add a guard in the `nowrap=false` branch that treats any `coin_time + relative` sum that would overflow as `u64::MAX` (saturating), matching the `nowrap=true` behavior:

```rust
// Instead of:
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {

// Use:
} else if timestamp < unspent.timestamp.saturating_add(seconds_relative) {
```

This eliminates the divergence between the two modes for overflow inputs.

3. Add a consensus-layer check that rejects `seconds_relative` or `height_relative` values that would overflow when added to any plausible coin timestamp/height, analogous to how `sanitize_uint` rejects `PositiveOverflow` for coin amounts.

### Proof of Concept

**Attacker-controlled CLVM puzzle** (pseudocode):
```
(mod () (list (list ASSERT_SECONDS_RELATIVE 0xFFFFFFFFFFFFFFFF)))
```

**Spend bundle**: spend the coin immediately after creation (no waiting).

**Node with `nowrap=false`**:
```
coin_time = 1_000_000  (creation timestamp)
seconds_relative = 0xFFFFFFFFFFFFFFFF
threshold = 1_000_000u64.wrapping_add(0xFFFFFFFFFFFFFFFF) = 999_999
current_timestamp = 1_000_001
check: 1_000_001 < 999_999 → false → Ok (spend accepted)
```

**Node with `nowrap=true`**:
```
threshold = 1_000_000u64.saturating_add(0xFFFFFFFFFFFFFFFF) = u64::MAX
check: 1_000_001 < u64::MAX → true → Err(AssertSecondsRelativeFailed)
```

The two nodes disagree on the validity of the block containing this spend, producing a chain split. [1](#0-0) [2](#0-1)

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

**File:** crates/chia-consensus/src/check_time_locks.rs (L301-306)
```rust
    // 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
    #[case::seconds_relative_wrap(
        Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
        Ok(()),
    )]
```

**File:** crates/chia-consensus/src/conditions.rs (L582-596)
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
```
