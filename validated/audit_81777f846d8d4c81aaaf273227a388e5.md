### Title
Integer Wrapping in Relative Timelock Arithmetic Bypasses `ASSERT_HEIGHT_RELATIVE` / `ASSERT_SECONDS_RELATIVE` and Causes Consensus Divergence - (File: `crates/chia-consensus/src/check_time_locks.rs`)

### Summary

`check_time_locks` contains a `nowrap: bool` parameter that selects between `saturating_add` (correct) and `wrapping_add` (unsafe) when computing relative timelock deadlines. When `nowrap=false`, an unprivileged CLVM spend embedding a near-maximum relative timelock value causes the deadline to wrap around to a small number, making the timelock appear already satisfied. Nodes running with `nowrap=false` accept the spend immediately; nodes running with `nowrap=true` reject it. This produces deterministic consensus divergence on any block containing such a spend, and constitutes a timelock validation bypass on nodes using the wrapping path.

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` accepts a `nowrap: bool` flag and branches on it for every relative timelock check:

```rust
if let Some(height_relative) = spend.height_relative {
    if nowrap {
        if prev_transaction_block_height
            < unspent.confirmed_block_index.saturating_add(height_relative)
        { ... }
    } else if prev_transaction_block_height
        < unspent.confirmed_block_index.wrapping_add(height_relative)  // ← wraps
    { ... }
}
``` [1](#0-0) 

The same pattern applies to `seconds_relative`, `before_height_relative`, and `before_seconds_relative`: [2](#0-1) 

The function is exposed to Python via `py_check_time_locks`, which forwards the caller-supplied `nowrap` flag unchanged: [3](#0-2) 

The Python type stub confirms `nowrap: bool` is a live, caller-controlled parameter: [4](#0-3) 

The test suite explicitly documents the divergent outcomes. For `ASSERT_HEIGHT_RELATIVE(0xFFFF_FFFF)` on a coin confirmed at height 100, checked at height 200:

- `nowrap=true` (saturating): `100.saturating_add(0xFFFF_FFFF) = u32::MAX`. `200 < u32::MAX` → **Err** (timelock enforced, correct).
- `nowrap=false` (wrapping): `100.wrapping_add(0xFFFF_FFFF) = 99`. `200 < 99` → **Ok** (timelock bypassed). [5](#0-4) 

For `ASSERT_BEFORE_HEIGHT_RELATIVE(0xFFFF_FFFF)` the inversion is equally harmful:

- `nowrap=true`: `100.saturating_add(0xFFFF_FFFF) = u32::MAX`. `200 >= u32::MAX` → **Ok** (spend allowed, correct).
- `nowrap=false`: `100.wrapping_add(0xFFFF_FFFF) = 99`. `200 >= 99` → **Err** (spend permanently rejected). [6](#0-5) 

The Python integration test confirms both behaviors are reachable at runtime: [7](#0-6) 

### Impact Explanation

**Timelock bypass (High):** A CLVM puzzle embedding `(ASSERT_HEIGHT_RELATIVE . 0xFFFFFFFF)` or `(ASSERT_SECONDS_RELATIVE . 0xFFFFFFFFFFFFFFFF)` is a valid, parseable condition. `sanitize_uint` accepts values up to `u32::MAX` for height and `u64::MAX` for seconds without error. On any node calling `check_time_locks` with `nowrap=false`, the wrapping arithmetic makes the deadline appear to be in the past, and the spend is accepted immediately regardless of how recently the coin was confirmed. This is a direct timelock condition validation bypass enabling unauthorized spend acceptance.

**Consensus divergence (Critical):** Because `nowrap` is a caller-supplied boolean, different full-node software versions or configurations may pass different values for the same block. A single spend bundle containing `ASSERT_HEIGHT_RELATIVE(u32::MAX)` will be accepted by `nowrap=false` nodes and rejected by `nowrap=true` nodes, producing a deterministic, permanent chain split on any block that includes such a transaction.

### Likelihood Explanation

The `nowrap` parameter is not a compile-time constant; it is a live Python-level argument forwarded through `py_check_time_locks`. The existence of the `nowrap=false` branch with `wrapping_add` in production code (not gated behind a test feature flag) and the explicit Python test coverage of both paths confirm this code path is reachable in deployed software. An attacker needs only to craft a coin spend with a near-maximum relative timelock value — a valid, unprivileged CLVM operation requiring no special keys or roles.

### Recommendation

1. Remove the `wrapping_add` branch entirely. Replace all four relative-timelock comparisons with `saturating_add` unconditionally, making `nowrap` always behave as `true`.
2. If backward compatibility with pre-fork blocks requires the wrapping behavior, gate it behind a hard-fork height check (analogous to `get_flags_for_height_and_constants`) rather than a caller-supplied boolean, so the behavior is deterministic and consensus-bound.
3. Add a consensus-level upper bound on relative timelock values (e.g., reject `height_relative > u32::MAX / 2`) to prevent the overflow class entirely, analogous to the Axiom fix of capping `queryDeadlineInterval` at `50_400`.

### Proof of Concept

```
# Coin confirmed at block 100.
# Puzzle solution emits: (ASSERT_HEIGHT_RELATIVE . 0xFFFFFFFF)
# This is a valid CLVM atom accepted by sanitize_uint(max_size=4).

# On a node calling check_time_locks(..., nowrap=False):
#   confirmed_block_index = 100
#   height_relative       = 0xFFFF_FFFF
#   wrapping_add(100, 0xFFFF_FFFF) = 99   (wraps mod 2^32)
#   prev_height (e.g. 101) < 99  →  False  →  Ok()   ← spend accepted immediately

# On a node calling check_time_locks(..., nowrap=True):
#   saturating_add(100, 0xFFFF_FFFF) = 0xFFFF_FFFF (u32::MAX)
#   prev_height (101) < u32::MAX  →  True  →  Err(AssertHeightRelativeFailed)
#                                              ← spend rejected

# The two nodes permanently disagree → consensus divergence.
``` [1](#0-0) [8](#0-7)

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

**File:** tests/test_check_time_locks.py (L238-271)
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
            # --- before_height_relative wrapping ---
            # check is >=, so wrapping to a small value causes failure
            # 10 + (2^32 - 11) = u32::MAX, no overflow, 15 >= u32::MAX -> Ok both
            (make_test_conds(before_height_relative=0xFFFF_FFF5), None, None),
            # 10 + (2^32 - 10) overflows to 0, wrapping: 15 >= 0 -> Err
            # saturating: 15 >= u32::MAX -> Ok
            (make_test_conds(before_height_relative=0xFFFF_FFF6), None, 131),
            # 10 + u32::MAX overflows to 9, wrapping: 15 >= 9 -> Err
            (make_test_conds(before_height_relative=0xFFFF_FFFF), None, 131),
            # --- before_seconds_relative wrapping ---
            # 10000 + (u64::MAX - 10000) = u64::MAX, no overflow, 10150 >= u64::MAX -> Ok both
            (
                make_test_conds(before_seconds_relative=0xFFFF_FFFF_FFFF_D8EF),
                None,
                None,
            ),
            # 10000 + (u64::MAX - 9999) overflows to 0, wrapping: 10150 >= 0 -> Err
            (make_test_conds(before_seconds_relative=0xFFFF_FFFF_FFFF_D8F0), None, 129),
            # 10000 + u64::MAX overflows to 9999, wrapping: 10150 >= 9999 -> Err
            (make_test_conds(before_seconds_relative=0xFFFF_FFFF_FFFF_FFFF), None, 129),
```
