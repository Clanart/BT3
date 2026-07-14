### Title
Integer Wrapping in `check_time_locks` Bypasses Relative Timelock Conditions in Legacy Consensus Mode — (File: `crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

In `check_time_locks`, when the `nowrap` flag is `false` (legacy pre-hard-fork consensus path), relative timelock arithmetic uses `wrapping_add` instead of `saturating_add`. A coin whose puzzle emits `ASSERT_SECONDS_RELATIVE(u64::MAX)` or `ASSERT_HEIGHT_RELATIVE(u32::MAX)` will have its computed threshold silently wrap around to a value smaller than the current block height/timestamp, causing the timelock guard to pass immediately. A coin that should be unspendable for an astronomically long time becomes spendable at any block.

---

### Finding Description

`check_time_locks` accepts a `nowrap: bool` parameter that selects between two arithmetic modes for relative timelock evaluation:

- `nowrap=true` → `saturating_add` (correct: clamps to `u64::MAX`/`u32::MAX`, timelock never passes)
- `nowrap=false` → `wrapping_add` (legacy: overflows silently, threshold wraps to a small value) [1](#0-0) 

For `ASSERT_SECONDS_RELATIVE`:

```
// nowrap=false path:
timestamp < unspent.timestamp.wrapping_add(seconds_relative)
```

If `unspent.timestamp = 1000` and `seconds_relative = u64::MAX`:
- `wrapping_add(u64::MAX)` = `1000 + 18446744073709550615 mod 2^64` = `999`
- The check becomes `timestamp < 999`
- Any block with `timestamp >= 999` passes — the timelock is bypassed entirely

The same wrapping flaw applies to `ASSERT_HEIGHT_RELATIVE` (u32 arithmetic): [2](#0-1) 

The test suite explicitly documents and confirms this divergence: [3](#0-2) 

The condition parser in `conditions.rs` accepts `u64::MAX` as a valid `ASSERT_SECONDS_RELATIVE` argument (it only rejects values that overflow the 8-byte encoding, i.e., `> u64::MAX`): [4](#0-3) 

So the full pipeline is: puzzle emits `ASSERT_SECONDS_RELATIVE(0xffff_ffff_ffff_ffff)` → condition parser accepts it → `check_time_locks(nowrap=false)` wraps the threshold to `coin_timestamp - 1` → spend is accepted immediately.

The Python binding exposes `nowrap` as a caller-controlled parameter with no enforcement: [5](#0-4) 

---

### Impact Explanation

Any coin whose puzzle generates a relative timelock condition with a near-maximum value (e.g., `ASSERT_SECONDS_RELATIVE(u64::MAX)`) is unprotected in the `nowrap=false` consensus path. Coins that rely solely on a relative timelock as their spending guard — such as vaulted coins, time-locked payment channels, or any puzzle that uses `ASSERT_SECONDS_RELATIVE`/`ASSERT_HEIGHT_RELATIVE` as the primary access control — can be spent before the intended unlock time. This constitutes a **timelock validation bypass enabling unauthorized spend acceptance**, matching the High impact tier.

Additionally, because `nowrap=true` and `nowrap=false` produce opposite results for the same spend bundle containing a large relative timelock, nodes running different modes will reach different consensus outcomes for the same block, constituting a **consensus divergence** risk.

---

### Likelihood Explanation

The `nowrap=false` path is the legacy pre-hard-fork mode. The full node (chia-blockchain Python) passes `nowrap` based on block height; old blocks are validated with `nowrap=false`. An attacker who controls a puzzle (or finds an existing coin) that emits `ASSERT_SECONDS_RELATIVE` with a value in the range `[u64::MAX - coin_timestamp + 1, u64::MAX]` can craft a spend that passes `check_time_locks` in legacy mode. The condition value `u64::MAX` is a single, easily known constant. No privileged access is required; only a valid CLVM solution for the target coin is needed.

---

### Recommendation

1. **Remove the `nowrap=false` branch entirely** once the hard fork has activated universally. The wrapping behavior has no correct use case — it was a pre-existing bug, not an intentional design.
2. Until removal, **assert `nowrap=true` in all consensus-critical callers** and document that `nowrap=false` is only retained for historical replay of pre-fork blocks where the buggy behavior was canonical.
3. Add a **parse-time rejection** in `conditions.rs` for `ASSERT_SECONDS_RELATIVE` / `ASSERT_HEIGHT_RELATIVE` values that would wrap given any plausible coin timestamp/height (e.g., values `> u64::MAX / 2`), or at minimum flag them as consensus-unsafe.

---

### Proof of Concept

Construct a CLVM puzzle that emits:
```
(ASSERT_SECONDS_RELATIVE . 0xffffffffffffffff)
```

Coin confirmed at timestamp `T = 1000`. Under `nowrap=false`:

```
threshold = 1000u64.wrapping_add(0xffff_ffff_ffff_ffff)
          = 1000 + 18446744073709550615 (mod 2^64)
          = 999
```

`check_time_locks` evaluates `current_timestamp < 999`. At any block with timestamp ≥ 999 (i.e., essentially any block after genesis), the check passes and the spend is accepted — despite the puzzle intending a ~584-billion-year lock. [6](#0-5)

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
