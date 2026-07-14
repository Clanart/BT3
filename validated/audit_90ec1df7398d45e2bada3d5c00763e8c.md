### Title
Integer Overflow in Relative Timelock Arithmetic Bypasses `ASSERT_HEIGHT_RELATIVE` / `ASSERT_SECONDS_RELATIVE` Conditions When `nowrap=false` — (File: `crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` contains two distinct arithmetic paths for relative timelock evaluation, selected by the caller-supplied `nowrap: bool` flag. When `nowrap=false`, the function uses `wrapping_add` for `height_relative` and `seconds_relative` comparisons. An attacker can craft a CLVM puzzle that emits `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` with a near-maximum value, causing the addition to wrap around to a small integer, making the timelock check pass immediately — bypassing what should be an astronomically long lock.

---

### Finding Description

`check_time_locks` accepts a `nowrap: bool` parameter that selects between two arithmetic modes for relative timelock evaluation:

- `nowrap=true` → `saturating_add` (safe: clamps at `u32::MAX` / `u64::MAX`)
- `nowrap=false` → `wrapping_add` (legacy: wraps around on overflow)

For `ASSERT_HEIGHT_RELATIVE`:

```rust
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}
```

If `confirmed_block_index = 100` and `height_relative = 0xFFFF_FFFF`, then `wrapping_add` produces `100u32.wrapping_add(0xFFFF_FFFF) = 99`. The check becomes `prev_height < 99`, which is `false` for any reasonable chain height (e.g., 200), so the function returns `Ok(())` — the timelock is bypassed.

The same flaw applies to `ASSERT_SECONDS_RELATIVE`:

```rust
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
    return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
}
```

With `coin_timestamp = 10000` and `seconds_relative = 0xFFFF_FFFF_FFFF_FFFF`, `wrapping_add` produces `9999`. The check `timestamp < 9999` is `false` for any live timestamp, so the lock passes immediately.

The code's own test suite explicitly documents and confirms this behavior:

```
// 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::height_relative_wrap(
    Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
    Ok(()),
)]
```

The Python binding `py_check_time_locks` exposes `nowrap` as a direct caller-controlled parameter, meaning the full node's Python code decides which arithmetic mode is used per block.

---

### Impact Explanation

This is a **High** impact finding matching the allowed scope: *"timelock or coin-id validation bypass enables unauthorized spend acceptance."*

A coin locked with `(ASSERT_HEIGHT_RELATIVE . 0xFFFFFFFF)` or `(ASSERT_SECONDS_RELATIVE . 0xFFFFFFFFFFFFFFFF)` is intended to be unspendable for ~136 years (height) or ~585 billion years (seconds). Under `nowrap=false`, the wrapping arithmetic makes the lock evaluate as already satisfied at any current height/timestamp, allowing the coin to be spent immediately. This enables unauthorized spend acceptance of time-locked coins.

---

### Likelihood Explanation

The `nowrap` parameter is explicitly exposed through the Python binding and is caller-controlled. The existence of both modes and the naming convention (`nowrap=True` as the "new" safe mode) strongly implies `nowrap=False` is used for pre-hard-fork blocks in the live full node. Any spend bundle processed under `nowrap=False` is vulnerable. An attacker only needs to craft a CLVM puzzle emitting a near-max relative timelock value — this requires no privileges, no key material, and no special network access.

---

### Recommendation

1. **Remove the `nowrap=false` code path entirely** once the hard fork activating `nowrap=true` is finalized and all nodes have upgraded. The wrapping arithmetic has no safe use case for timelock enforcement.
2. **Until removal**, add a guard in `check_time_locks` that rejects any `height_relative` or `seconds_relative` value that would overflow when added to the coin's confirmed height/timestamp, regardless of the `nowrap` flag.
3. **Document explicitly** which blocks use `nowrap=false` and audit whether any live consensus path still passes `nowrap=false` for spends that could carry attacker-controlled condition arguments.

---

### Proof of Concept

Attacker creates a coin with puzzle:

```clvm
(mod () (list (list 82 4294967295)))  ; ASSERT_HEIGHT_RELATIVE 0xFFFFFFFF
```

Coin is confirmed at block height 100. Full node calls `check_time_locks` with `nowrap=false` at current height 200:

```
confirmed_block_index = 100
height_relative = 0xFFFF_FFFF (= 4294967295)
100u32.wrapping_add(4294967295) = 99
check: 200 < 99  →  false  →  Ok(())  ← timelock bypassed
```

With `nowrap=true` (saturating):
```
100u32.saturating_add(4294967295) = u32::MAX = 4294967295
check: 200 < 4294967295  →  true  →  Err(AssertHeightRelativeFailed)  ← correctly blocked
```

The divergence is confirmed by the unit test at: [1](#0-0) 

The wrapping arithmetic paths are at: [2](#0-1) [3](#0-2) 

The Python-exposed binding that makes `nowrap` caller-controlled: [4](#0-3) 

The public Python API stub confirming `nowrap` is an unprivileged caller parameter: [5](#0-4)

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
