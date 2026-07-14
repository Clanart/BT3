### Title
Integer Overflow in `check_time_locks` Relative Timelock Validation Allows Timelock Bypass — (File: `crates/chia-consensus/src/check_time_locks.rs`)

---

### Summary

When `check_time_locks` is called with `nowrap=false` (the pre-soft-fork behavior), relative timelock deadline computation uses `wrapping_add` instead of `saturating_add`. An unprivileged attacker can embed a crafted `ASSERT_SECONDS_RELATIVE` or `ASSERT_HEIGHT_RELATIVE` value in a CLVM puzzle such that the addition overflows and the computed deadline wraps to a value already in the past. The timelock check then passes immediately, bypassing the intended delay and enabling unauthorized spend acceptance — directly analogous to the Taiko report's invocation-delay bypass after an unpause.

---

### Finding Description

In `check_time_locks`, the relative timelock checks branch on the `nowrap` flag:

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
``` [1](#0-0) 

The same wrapping pattern applies to `height_relative`:

```rust
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}
``` [2](#0-1) 

**Overflow bypass mechanics:**

Let `coin_timestamp = T`. An attacker sets `seconds_relative = u64::MAX - T + 1`. Then:

```
T.wrapping_add(u64::MAX - T + 1) = 0
```

The check becomes `current_timestamp < 0`, which is always `false` for a `u64`. The spend is accepted **immediately**, regardless of how much time has elapsed since the coin was confirmed. The intended timelock (which should require waiting ~584 billion years for `seconds_relative = u64::MAX`) is completely bypassed.

The codebase's own test suite documents this exact divergence:

```
// 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::seconds_relative_wrap(
    Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),  // nowrap=true
    Ok(()),                                                            // nowrap=false
)]
``` [3](#0-2) 

The same overflow bypass applies to `height_relative` (u32 wrapping):

```
// 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
#[case::height_relative_wrap(
    Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),   // nowrap=true
    Ok(()),                                                            // nowrap=false
)]
``` [4](#0-3) 

The `nowrap` parameter is a Python-controlled boolean exposed via the `py_check_time_locks` binding:

```rust
pub fn py_check_time_locks(
    removal_coin_records: HashMap<Bytes32, CoinRecord>,
    bundle_conds: &OwnedSpendBundleConditions,
    prev_transaction_block_height: u32,
    timestamp: u64,
    nowrap: bool,
) -> PyResult<Option<u32>> {
``` [5](#0-4) 

There is no `ConsensusFlags` bit or height-gated activation in the Rust layer that enforces `nowrap=true`. The decision is entirely delegated to the Python caller, meaning any node running with `nowrap=false` (the pre-soft-fork default) is vulnerable. [6](#0-5) 

---

### Impact Explanation

**Timelock validation bypass enabling unauthorized spend acceptance (High).**

A coin whose puzzle enforces `ASSERT_SECONDS_RELATIVE(N)` — intended to lock funds for a specific duration — can be spent immediately by an attacker who sets `N = u64::MAX - coin_timestamp + 1`. The wrapping arithmetic makes the computed deadline `0`, which is always in the past. The spend is accepted by any node running `nowrap=false` without the required delay having elapsed.

Additionally, nodes running `nowrap=false` and nodes running `nowrap=true` will reach **opposite conclusions** about the validity of such a spend, producing deterministic consensus divergence: one set of nodes accepts the block containing the spend; the other rejects it. This can halt the chain.

---

### Likelihood Explanation

- The attacker controls the `seconds_relative` argument directly inside the CLVM puzzle — no privileged access, governance role, or key material is required.
- The overflow value is trivially computable from the coin's on-chain `confirmed_block_index` / `timestamp`, both of which are public.
- The vulnerability is active on any node calling `check_time_locks` with `nowrap=false`. Since `nowrap` is not gated by any `ConsensusFlags` or height check in the Rust layer, the window of exposure depends entirely on whether the Python full node has activated the soft fork. During any transition period, both behaviors coexist across the network.
- The `ASSERT_HEIGHT_RELATIVE` variant requires only a u32 overflow (much smaller values, e.g., `height_relative = u32::MAX - confirmed_block_index + 1`), making it even easier to trigger.

---

### Recommendation

1. **Remove the `nowrap=false` / `wrapping_add` path entirely** once the soft fork has activated. The wrapping behavior has no safe use case for timelock enforcement.
2. **Gate `nowrap` on a `ConsensusFlags` bit** (e.g., `ConsensusFlags::NOWRAP_TIMELOCKS`) that is activated at a specific block height via `get_flags_for_height_and_constants`, rather than delegating the decision to the Python caller. This ensures all nodes transition atomically at the same height.
3. **Reject `seconds_relative` or `height_relative` values that would overflow** at condition-parse time (in `conditions.rs`) rather than deferring the check to `check_time_locks`. A `PositiveOverflow` from `sanitize_uint` for `ASSERT_SECONDS_RELATIVE` already returns an error; the same treatment should apply to values that overflow when added to any plausible coin timestamp.

---

### Proof of Concept

**Setup:**
- Coin confirmed at timestamp `T = 1000` (block height `H = 100`).
- Attacker embeds `ASSERT_SECONDS_RELATIVE(18446744073709550616)` in the puzzle (i.e., `u64::MAX - 999`).

**On a node with `nowrap=false`:**
```
deadline = 1000u64.wrapping_add(18446744073709550616) = 0
check:    current_timestamp < 0  →  false  →  spend ACCEPTED (timelock bypassed)
```

**On a node with `nowrap=true`:**
```
deadline = 1000u64.saturating_add(18446744073709550616) = u64::MAX
check:    current_timestamp < u64::MAX  →  true  →  spend REJECTED (timelock enforced)
```

The attacker submits the spend immediately after coin creation. Old nodes accept it; upgraded nodes reject it. The chain forks. The attacker's coin — which was supposed to be time-locked — is spent without the delay being honoured, directly mirroring the Taiko report's scenario where the preferred-executor window is lost because pause time is not subtracted from the delay. [1](#0-0) [7](#0-6)

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

**File:** crates/chia-consensus/src/spendbundle_validation.rs (L61-102)
```rust
pub fn get_flags_for_height_and_constants(
    prev_tx_height: u32,
    constants: &ConsensusConstants,
) -> ConsensusFlags {
    //  the hard-fork initiated with 2.0. To activate June 2024
    //  * costs are ascribed to some unknown condition codes, to allow for
    // soft-forking in new conditions with cost
    //  * a new condition, SOFTFORK, is added which takes a first parameter to
    //    specify its cost. This allows soft-forks similar to the softfork
    //    operator
    //  * BLS operators introduced in the soft-fork (behind the softfork
    //    guard) are made available outside of the guard.
    //  * division with negative numbers are allowed, and round toward
    //    negative infinity
    //  * AGG_SIG_* conditions are allowed to have unknown additional
    //    arguments
    //  * Allow the block generator to be serialized with the improved clvm
    //   serialization format (with back-references)

    // The soft fork initiated with 2.5.0. The activation date is still TBD.
    // Adds a new keccak256 operator under the softfork guard with extension 1.
    // This operator can be hard forked in later, but is not included in a hard fork yet.

    // In hard fork 2, we enable the keccak operator outside the softfork guard
    let mut flags = ConsensusFlags::empty();
    if prev_tx_height >= constants.hard_fork2_height {
        flags |= ConsensusFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD
            | ConsensusFlags::COST_CONDITIONS
            | ConsensusFlags::ENABLE_SECP_OPS
            | ConsensusFlags::RELAXED_BLS;
    }

    if prev_tx_height >= constants.soft_fork8_height {
        flags |= ConsensusFlags::DISABLE_OP;
    }

    if prev_tx_height >= constants.soft_fork9_height {
        flags |= ConsensusFlags::SIMPLE_GENERATOR
            | ConsensusFlags::CANONICAL_INTS
            | ConsensusFlags::LIMIT_SPENDS;
    }
    flags
```
