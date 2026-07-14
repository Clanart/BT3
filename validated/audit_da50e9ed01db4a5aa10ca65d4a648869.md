### Title
Wrapping Arithmetic in `check_time_locks` Enables Relative Timelock Bypass Under Legacy `nowrap=false` Mode - (File: crates/chia-consensus/src/check_time_locks.rs)

### Summary
`check_time_locks` contains two arithmetic modes for relative timelock evaluation, controlled by a `nowrap` boolean. The legacy `nowrap=false` path uses `wrapping_add`, which causes near-maximum relative timelock values to silently wrap around to small integers. This makes `ASSERT_HEIGHT_RELATIVE` and `ASSERT_SECONDS_RELATIVE` timelocks trivially bypassable, and makes `ASSERT_BEFORE_HEIGHT_RELATIVE` / `ASSERT_BEFORE_SECONDS_RELATIVE` conditions permanently unsatisfiable. If nodes disagree on which mode to use, consensus diverges on the same spend bundle.

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` evaluates relative timelocks using one of two arithmetic strategies depending on the `nowrap` parameter:

- `nowrap=true` → `saturating_add` (correct, clamped behavior)
- `nowrap=false` → `wrapping_add` (legacy, wrapping behavior)

For `ASSERT_HEIGHT_RELATIVE` under `nowrap=false`:

```rust
} else if prev_transaction_block_height
    < unspent.confirmed_block_index.wrapping_add(height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
}
```

If `confirmed_block_index + height_relative` overflows `u32`, the result wraps to a value smaller than `confirmed_block_index`. For example, with `confirmed_block_index = 100` and `height_relative = 0xFFFF_FFFF`:

```
100u32.wrapping_add(0xFFFF_FFFF) = 99
```

The check becomes `prev_height < 99`, which is `false` for any realistic block height, so the spend is **accepted** — the timelock is completely bypassed.

The inverse failure occurs for `ASSERT_BEFORE_HEIGHT_RELATIVE` under `nowrap=false`:

```rust
} else if prev_transaction_block_height
    >= unspent.confirmed_block_index.wrapping_add(before_height_relative)
{
    return Err(ValidationErr::Err(ErrorCode::AssertBeforeHeightRelativeFailed));
}
```

With the same overflow, the check becomes `prev_height >= 99`, which is `true` for any realistic block height, so the spend is **permanently rejected** — the coin is locked forever.

The same pattern applies to `seconds_relative` (`u64`) and `before_seconds_relative`. The code and tests explicitly document that the two modes produce opposite consensus outcomes for near-max values: [1](#0-0) [2](#0-1) 

The tests confirm the divergence explicitly: [3](#0-2) [4](#0-3) 

The `nowrap` parameter is not derived from any consensus constant or block height inside the Rust code. It is passed entirely from the Python full node via the exposed binding: [5](#0-4) [6](#0-5) 

`get_flags_for_height_and_constants`, which derives all other consensus mode flags from block height, does not control `nowrap` at all: [7](#0-6) 

### Impact Explanation

**Timelock bypass (High):** Under `nowrap=false`, any spend bundle containing `ASSERT_HEIGHT_RELATIVE` or `ASSERT_SECONDS_RELATIVE` with a near-max value (specifically any value `v` such that `confirmed_index + v` overflows the integer type) passes the timelock check immediately, regardless of the actual block height or timestamp. The coin can be spent before its intended lock expires.

**Consensus divergence (Critical):** If any two nodes call `check_time_locks` with different `nowrap` values for the same spend bundle containing such a condition, they reach opposite validity conclusions. One node accepts the spend; the other rejects it. This is a deterministic, reproducible chain split triggered by a single crafted spend bundle.

**Permanent coin lock:** Under `nowrap=false`, `ASSERT_BEFORE_HEIGHT_RELATIVE` / `ASSERT_BEFORE_SECONDS_RELATIVE` with a near-max value causes the coin to be permanently unspendable on nodes using the legacy mode.

### Likelihood Explanation

The `nowrap` parameter is a Python-side responsibility with no enforcement in the Rust consensus layer. Any transition period — where some nodes pass `nowrap=True` and others pass `nowrap=False` — is a live consensus-divergence window. An attacker who knows the transition is occurring can craft a spend with `height_relative = u32::MAX` (a valid, parseable CLVM atom) to deterministically split the network. The condition value `0xFFFF_FFFF` is a 4-byte atom, well within the 4-byte limit enforced by `sanitize_uint` for height conditions: [8](#0-7) 

### Recommendation

1. **Tie `nowrap` to a consensus height inside the Rust layer.** Add a `nowrap` flag to `ConsensusFlags` (analogous to `COST_CONDITIONS`, `SIMPLE_GENERATOR`, etc.) and derive it from block height inside `get_flags_for_height_and_constants`. Pass it through to `check_time_locks` from the Rust consensus path, eliminating the Python-side responsibility. [9](#0-8) [10](#0-9) 

2. **Remove the `nowrap=false` path** once the hard fork activating saturating semantics is finalized, eliminating the wrapping code entirely.

3. **Reject near-max relative timelock values at parse time** (e.g., values ≥ `u32::MAX - max_chain_height`) as an additional defense-in-depth measure.

### Proof of Concept

```
Coin confirmed at block height 100.
Spend bundle contains: ASSERT_HEIGHT_RELATIVE = 0xFFFF_FFFF (u32::MAX)

Node A (nowrap=false, legacy):
  100u32.wrapping_add(0xFFFF_FFFF) = 99
  check: prev_height(200) < 99  →  false  →  ACCEPTED (timelock bypassed)

Node B (nowrap=true, new):
  100u32.saturating_add(0xFFFF_FFFF) = u32::MAX (4_294_967_295)
  check: prev_height(200) < 4_294_967_295  →  true  →  REJECTED (correct)

Result: Node A and Node B disagree on the validity of the same spend bundle.
        Node A accepts a spend that bypasses a timelock that has not expired.
        The chain forks deterministically.
``` [1](#0-0)

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

**File:** crates/chia-consensus/src/check_time_locks.rs (L327-331)
```rust
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

**File:** crates/chia-consensus/src/spendbundle_validation.rs (L61-103)
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
}
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

**File:** crates/chia-consensus/src/flags.rs (L7-58)
```rust
bitflags! {
    /// Full flag set for CLVM execution and consensus (condition parsing, validation, generator mode).
    /// Combines flags from clvmr (lower bytes) and consensus (upper bytes).
    /// The end goal should be to make these flags independent, but we still
    /// have at least one quirk in chia-protocol's Program::run_rust() where it
    /// would be ideal to take Consensusflags, but it can't depend on
    /// chia-consensus, so it has to take ClvmFlags instead. those aren't exposed
    /// to python, so it relies on these flags matching.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub struct ConsensusFlags: u32 {
        // Flags from clvmr (chia_dialect)
        // we still rely on these bits matching exactly the flags in clvm_rs
        // via the python binding, which "launders" the type of the flags
        const CANONICAL_INTS = 0x0001;
        const NO_UNKNOWN_OPS = 0x0002;
        const LIMIT_HEAP = 0x0004;
        const RELAXED_BLS = 0x0008;
        const LIMIT_SOFTFORK = 0x0010;
        const ENABLE_GC = 0x0020;
        const ENABLE_KECCAK_OPS_OUTSIDE_GUARD = 0x0100;
        const DISABLE_OP = 0x0200;
        const ENABLE_SHA256_TREE = 0x0400;
        const ENABLE_SECP_OPS = 0x0800;
        const MALACHITE = 0x1000;

        // Consensus flags
        /// Skip validating AGG_SIG / condition signatures.
        const DONT_VALIDATE_SIGNATURE = 0x1_0000;

        /// Unknown condition codes are disallowed (mempool-mode).
        const NO_UNKNOWN_CONDS = 0x2_0000;

        /// Compute condition fingerprints for spends eligible for dedup.
        const COMPUTE_FINGERPRINT = 0x4_0000;

        /// Conditions require the exact supported argument count (mempool-mode).
        const STRICT_ARGS_COUNT = 0x8_0000;

        /// Add flat cost to conditions (active after hard fork 2).
        const COST_CONDITIONS = 0x80_0000;

        /// Simpler generator rules (hard fork behavior).
        const SIMPLE_GENERATOR = 0x100_0000;

        /// Limit the number of spends per block.
        const LIMIT_SPENDS = 0x200_0000;

        /// After the generator-identity hard fork, generators must be validated from
        /// the INTERNED (canonical) tree so atom/pair limits and cost apply to the same
        /// structure independent of serialization.
        const INTERNED_GENERATOR = 0x0800_0000;
    }
```
