### Title
`INTERNED_GENERATOR` Flag Defined and Implemented but Never Activated in `get_flags_for_height_and_constants` — (File: `crates/chia-consensus/src/spendbundle_validation.rs`)

---

### Summary

`ConsensusFlags::INTERNED_GENERATOR` is declared with an explicit security purpose and has a full implementation branch in `run_block_generator2`, but `get_flags_for_height_and_constants` — the sole canonical source of flags for block validation — never sets it for any height threshold. The "generator-identity hard fork" protection is therefore permanently inactive, making the flag and its implementation dead code in the consensus path.

---

### Finding Description

`ConsensusFlags::INTERNED_GENERATOR` is defined in `flags.rs` with the comment:

> "After the generator-identity hard fork, generators must be validated from the INTERNED (canonical) tree so atom/pair limits and cost apply to the same structure independent of serialization." [1](#0-0) 

The implementation branch in `run_block_generator2` correctly handles this flag: when set, it deserializes the generator into a temporary allocator, interns the tree into its canonical form, and computes the byte cost from `interned_vbytes` (the canonical tree size). When the flag is absent, it falls back to computing byte cost from `program.len()` — the raw serialized length, which can be much smaller than the canonical form when back-references are used. [2](#0-1) 

However, `get_flags_for_height_and_constants` — which is called by every block validation entry point — activates flags only for three height thresholds (`hard_fork2_height`, `soft_fork8_height`, `soft_fork9_height`) and never includes `INTERNED_GENERATOR` in any of them: [3](#0-2) 

There is no corresponding height constant in `ConsensusConstants` for this flag, and no code path in production block validation ever sets it. The flag is defined, the enforcement code exists, but the activation is absent — a direct structural analog to the SuperPool `togglePause()` issue.

---

### Impact Explanation

Without `INTERNED_GENERATOR`, the byte cost charged for a block generator is `program.len() * cost_per_byte`, where `program` is the raw serialized bytes. Because `node_from_bytes_backrefs` is used to deserialize the generator (expanding back-references), the in-memory canonical tree can be arbitrarily larger than the serialized form. A generator serialized with aggressive back-references could present a small serialized size (e.g., 2 KB) while expanding to a canonical tree orders of magnitude larger.

The consequence is systematic byte-cost underestimation: the `cost_left` budget is reduced by less than the canonical tree warrants, leaving more headroom for puzzle execution cost. A block producer can craft a generator that passes the `max_block_cost_clvm` check while the true canonical cost would exceed it. This maps to the allowed impact:

> **High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data.**

The cost computed on serialized bytes diverges from the cost that would be computed on the canonical tree, creating a systematic discrepancy in consensus-critical cost accounting.

---

### Likelihood Explanation

Exploiting this requires winning the Chia proof-of-space lottery to produce a block — not a truly privileged role (any farmer can win), but not unprivileged either. The attack is passive: a farmer simply serializes their generator with maximal back-reference compression. No key compromise, network-level attack, or governance action is needed. The likelihood is therefore **medium**: the precondition is probabilistic (winning a block slot) but the technique is straightforward once that precondition is met.

---

### Recommendation

Add `INTERNED_GENERATOR` to the flag set returned by `get_flags_for_height_and_constants` at the appropriate hard-fork height (add a corresponding `interned_generator_height` field to `ConsensusConstants`), mirroring how `SIMPLE_GENERATOR`, `CANONICAL_INTS`, and `LIMIT_SPENDS` are activated at `soft_fork9_height`:

```rust
if prev_tx_height >= constants.interned_generator_height {
    flags |= ConsensusFlags::INTERNED_GENERATOR;
}
```

Until a height is chosen, the flag and its implementation branch in `run_block_generator2` provide no protection.

---

### Proof of Concept

1. Construct a block generator that, when serialized with back-references, is N bytes but whose canonical (interned) tree is M >> N bytes.
2. Submit the block through the normal validation path, which calls `get_flags_for_height_and_constants` → `run_block_generator2`.
3. Because `INTERNED_GENERATOR` is never set, `base_cost = N * cost_per_byte` is subtracted from `cost_left`.
4. The remaining `cost_left` budget is `(M - N) * cost_per_byte` larger than it should be.
5. Puzzles within the block can consume this extra budget, allowing the block's true canonical cost to exceed `max_block_cost_clvm` while still passing validation. [4](#0-3) [5](#0-4)

### Citations

**File:** crates/chia-consensus/src/flags.rs (L54-58)
```rust
        /// After the generator-identity hard fork, generators must be validated from
        /// the INTERNED (canonical) tree so atom/pair limits and cost apply to the same
        /// structure independent of serialization.
        const INTERNED_GENERATOR = 0x0800_0000;
    }
```

**File:** crates/chia-consensus/src/run_block_generator.rs (L224-243)
```rust
    let (mut a, base_cost, program) = if flags.contains(ConsensusFlags::INTERNED_GENERATOR) {
        let mut decode_allocator = Allocator::new();
        let program_node = node_from_bytes_backrefs(&mut decode_allocator, program)?;
        let interned = intern_tree_limited(&decode_allocator, program_node, u32::MAX as usize)
            .map_err(|_| ValidationErr::Err(ErrorCode::GeneratorRuntimeError))?;
        let cost = interned_vbytes(&interned) * constants.cost_per_byte;
        let InternedTree {
            allocator, root, ..
        } = interned;
        drop(decode_allocator);
        (allocator, cost, root)
    } else {
        let mut a = make_allocator(flags);
        let byte_cost = program.len() as u64 * constants.cost_per_byte;
        let program = node_from_bytes_backrefs(&mut a, program)?;
        (a, byte_cost, program)
    };

    let mut cost_left = max_cost;
    subtract_cost(&mut cost_left, base_cost)?;
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
