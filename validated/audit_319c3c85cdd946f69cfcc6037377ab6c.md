### Title
Integer Overflow via `wrapping_add` in Relative Timelock Arithmetic Enables Timelock Bypass and Consensus Divergence — (`File: crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

The `check_time_locks` function in `crates/chia-consensus/src/check_time_locks.rs` contains two distinct arithmetic paths for relative timelock validation, controlled by a caller-supplied `nowrap: bool` flag. When `nowrap=false`, the function uses `wrapping_add` for `ASSERT_HEIGHT_RELATIVE` and `ASSERT_SECONDS_RELATIVE` checks. An unprivileged attacker can craft a spend bundle with a near-maximum relative timelock value (e.g., `height_relative = 0xffff_ffff`) to cause integer overflow, wrapping the computed threshold to a small value and bypassing the timelock entirely on nodes running with `nowrap=false`. Nodes running with `nowrap=true` (saturating) will correctly reject the same spend. This produces deterministic consensus divergence between nodes depending on which arithmetic mode they apply.

---

### Finding Description

In `check_time_locks`, lines 55–77, the relative timelock checks branch on `nowrap`:

```rust
if let Some(height_relative) = spend.height_relative {
    if nowrap {
        if prev_transaction_block_height
            < unspent.confirmed_block_index.saturating_add(height_relative)
        { ... }
    } else if prev_transaction_block_height
        < unspent.confirmed_block_index.wrapping_add(height_relative)
    { ... }
}
```

When `nowrap=false`, `wrapping_add` is used. For a coin confirmed at block height `H` and a spend asserting `height_relative = 0xffff_ffff` (u32::MAX):

```
H.wrapping_add(0xffff_ffff) = H - 1  (mod 2^32)
```

The check becomes `prev_height < H - 1`, which passes for any `prev_height >= H - 1`. The timelock is effectively bypassed: the coin is spendable almost immediately after confirmation, regardless of the enormous relative height value the puzzle author intended.

The same overflow pattern applies to `seconds_relative` (u64, lines 70–77) and to the inverse `before_height_relative` / `before_seconds_relative` checks (lines 79–111), where wrapping produces the opposite error — valid spends are incorrectly rejected.

The `nowrap` parameter is passed from the Python layer through `py_check_time_locks` (lines 122–141), which is the public binding consumed by chia-blockchain node software. The value of `nowrap` is determined by the Python caller at runtime, not hardcoded in the Rust consensus logic. The tests in the same file explicitly document and confirm the divergent outcomes:

```rust
// 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::height_relative_wrap(
    Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),  // nowrap=true
    Ok(()),  // nowrap=false: ACCEPTED despite enormous relative height
)]
```

---

### Impact Explanation

**Timelock validation bypass (High):** A spend bundle with `ASSERT_HEIGHT_RELATIVE 0xffffffff` submitted to a node running `nowrap=false` will be accepted at any block height ≥ `confirmed_height - 1`. The coin is spendable far earlier than the puzzle author intended, constituting unauthorized spend acceptance.

**Consensus divergence (Critical):** The same spend bundle is accepted by nodes with `nowrap=false` and rejected by nodes with `nowrap=true`. If any production nodes disagree on the `nowrap` value for the same block — which is possible during a soft-fork transition period, or if the Python caller does not enforce a uniform activation height — the network will split on whether the block containing this spend is valid.

Both impacts fall within the allowed scope:
- *High*: timelock condition validation bypass enables unauthorized spend acceptance.
- *Critical*: a valid unprivileged spend bundle triggers deterministic consensus divergence.

---

### Likelihood Explanation

The `nowrap` flag is not enforced inside the Rust consensus library itself — it is a caller-supplied parameter. Any node or integration that calls `check_time_locks` (or `py_check_time_locks`) with `nowrap=False` is vulnerable. During a soft-fork activation window, nodes on different software versions may apply different `nowrap` values to the same block, making the divergence reachable without any privileged access. The attacker only needs to submit a spend bundle with a near-maximum relative timelock value to any mempool-accepting node.

---

### Recommendation

1. Remove the `wrapping_add` path entirely. The `nowrap=false` branch should be deprecated and deleted; `saturating_add` is the correct and safe behavior for all timelock arithmetic.
2. If backward compatibility with historical blocks requires the wrapping behavior, gate it strictly on a consensus-layer block height constant (not a caller-supplied boolean), so all nodes apply the same arithmetic for the same block deterministically.
3. Add a consensus-layer assertion or type-level enforcement that prevents the `nowrap=false` path from being used for blocks above the soft-fork activation height.

---

### Proof of Concept

**Setup:** Coin confirmed at block height 100. Attacker crafts a spend with condition `ASSERT_HEIGHT_RELATIVE 0xffffffff`.

**On a node with `nowrap=false` (wrapping):**
```
threshold = 100u32.wrapping_add(0xffff_ffff) = 99
check: prev_height < 99  →  passes for prev_height >= 99
Result: spend ACCEPTED at block 99+
```

**On a node with `nowrap=true` (saturating):**
```
threshold = 100u32.saturating_add(0xffff_ffff) = u32::MAX = 4294967295
check: prev_height < 4294967295  →  fails for any realistic block height
Result: spend REJECTED
```

The two nodes reach opposite conclusions for the same spend bundle, producing a chain split. The attacker controls the outcome by choosing `height_relative` values that overflow under `wrapping_add` but saturate under `saturating_add`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crates/chia-consensus/src/check_time_locks.rs (L326-331)
```rust
    // 200 >= 100 + 0xffff_ffff -> Ok with nowrap (saturates), Err without (wraps)
    #[case::before_height_relative_wrap(
        Osc { before_height_relative: Some(0xffff_ffff), ..Default::default() },
        Ok(()),
        Err(ValidationErr::Err(ErrorCode::AssertBeforeHeightRelativeFailed)),
    )]
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L351-356)
```rust
    // 2000 >= 1000 + u64::MAX -> Ok with nowrap (saturates), Err without (wraps)
    #[case::before_seconds_relative_wrap(
        Osc { before_seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
        Ok(()),
        Err(ValidationErr::Err(ErrorCode::AssertBeforeSecondsRelativeFailed)),
    )]
```
