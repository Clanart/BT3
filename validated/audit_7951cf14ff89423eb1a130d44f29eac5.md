Looking at the `parse()` function for v0 and the `compute_plot_id_v1` function carefully:

The code is clear. Let me trace the exact path:

**Parse path for v0** (lines 300–315): No guard requiring at least one pool field to be set. The `v0_neither_lenient` roundtrip test case explicitly confirms this is accepted by design.

**v1 path** (lines 322–324): Has the guard `if pool_public_key.is_some() == pool_contract_puzzle_hash.is_some() { return Err(Error::InvalidPoS); }` — but this guard is absent for v0.

**`compute_plot_id_v1`** (lines 85–91): Panics unconditionally when both are `None`.

The invariant break is real and concrete. However, the impact classification determines whether this is a bounty-grade finding.

---

### Title
Crafted v0 `ProofOfSpace` with both pool fields absent parses successfully but panics in `compute_plot_id_v1`, enabling node crash via network input — (`crates/chia-protocol/src/proof_of_space.rs`)

### Summary

The `parse()` implementation for v0 `ProofOfSpace` accepts structs where both `pool_public_key` and `pool_contract_puzzle_hash` are `None`, but `compute_plot_id_v1` unconditionally panics when called on such a struct. An unprivileged attacker can craft a serialized v0 `ProofOfSpace` with both pool fields absent, submit it as part of a block or proof, and cause any node that calls `compute_plot_id()` during validation to panic.

### Finding Description

The `parse()` function for v0 (`version == 0`) applies no constraint on the pool fields: [1](#0-0) 

By contrast, the v1 path enforces exactly-one-of: [2](#0-1) 

The roundtrip test `v0_neither_lenient` explicitly confirms the parser accepts v0 with neither field set: [3](#0-2) 

When `compute_plot_id()` is subsequently called on such a struct, it dispatches to `compute_plot_id_v1`, which panics: [4](#0-3) 

### Impact Explanation

A Rust `panic!` in a thread not wrapped in `catch_unwind` terminates the thread (and potentially the process). Any Rust-native node that receives a crafted block containing such a `ProofOfSpace` and calls `compute_plot_id()` during consensus validation will crash. If the panic is not caught, this is a remotely-triggerable node crash via a single crafted network message.

For the Python-binding path (`py_compute_plot_id`), PyO3 catches Rust panics and converts them to Python `RuntimeError`, so Python-based nodes would raise an exception rather than crash — but the exception may still be unhandled and crash the validation pipeline. [5](#0-4) 

### Likelihood Explanation

- Crafting the serialized bytes is trivial: set the `pool_public_key` Option prefix to `0x00` (absent) and the `pool_contract_puzzle_hash` prefix byte to `0x00` (absent, version=0).
- No authentication or privilege is required to submit a block/proof to a node.
- The panic is deterministic and reproducible.

### Recommendation

Add the same exactly-one-of guard to the v0 parse path:

```rust
if version == 0 {
    if pool_public_key.is_some() == pool_contract_puzzle_hash.is_some() {
        return Err(Error::InvalidPoS);
    }
    // ... rest of v0 parse
}
```

Alternatively, replace the `panic!` in `compute_plot_id_v1` with a `Result`-returning signature so callers can handle the error gracefully.

### Proof of Concept

```rust
use chia_protocol::ProofOfSpace;
use chia_traits::Streamable;
use std::io::Cursor;

// Craft: challenge(32) + pool_pk_absent(0x00) + contract_absent_v0(0x00) + plot_pk(48) + size(1) + proof_len(4) + proof(N)
// pool_public_key prefix = 0x00 (None), contract prefix = 0x00 (None, version=0)
let mut buf = vec![0u8; 32]; // challenge
buf.push(0x00); // pool_public_key = None
buf.push(0x00); // pool_contract_puzzle_hash = None, version = 0
buf.extend_from_slice(&[0u8; 48]); // plot_public_key (invalid but parseable if TRUSTED=false skips BLS check)
buf.push(32u8); // size
buf.extend_from_slice(&[0x00, 0x00, 0x00, 0x01, 0x00]); // proof (1 byte)

let pos = ProofOfSpace::parse::<false>(&mut Cursor::new(&buf)).unwrap();
// Both pool fields are None, version == 0
assert!(pos.pool_public_key.is_none());
assert!(pos.pool_contract_puzzle_hash.is_none());

// This panics:
let _ = pos.compute_plot_id(); // panic: "invalid proof of space. Neither pool pk nor contract puzzle hash set"
``` [6](#0-5)

### Citations

**File:** crates/chia-protocol/src/proof_of_space.rs (L85-91)
```rust
    if let Some(pool_pk) = pool_pk {
        pool_pk.update_digest(&mut ctx);
    } else if let Some(contract_ph) = pool_contract {
        contract_ph.update_digest(&mut ctx);
    } else {
        panic!("invalid proof of space. Neither pool pk nor contract puzzle hash set");
    }
```

**File:** crates/chia-protocol/src/proof_of_space.rs (L137-144)
```rust
    pub fn compute_plot_id(&self) -> Bytes32 {
        if self.version == 0 {
            // v1 proofs
            compute_plot_id_v1(
                &self.plot_public_key,
                self.pool_public_key.as_ref(),
                self.pool_contract_puzzle_hash.as_ref(),
            )
```

**File:** crates/chia-protocol/src/proof_of_space.rs (L205-208)
```rust
    #[pyo3(name = "compute_plot_id")]
    pub fn py_compute_plot_id(&self) -> Bytes32 {
        self.compute_plot_id()
    }
```

**File:** crates/chia-protocol/src/proof_of_space.rs (L300-315)
```rust
        if version == 0 {
            let size = <u8 as Streamable>::parse::<TRUSTED>(input)?;
            let proof = <Bytes as Streamable>::parse::<TRUSTED>(input)?;

            Ok(ProofOfSpace {
                challenge,
                pool_public_key,
                pool_contract_puzzle_hash,
                plot_public_key,
                version,
                plot_index: 0,
                meta_group: 0,
                strength: 0,
                size,
                proof,
            })
```

**File:** crates/chia-protocol/src/proof_of_space.rs (L322-324)
```rust
            if pool_public_key.is_some() == pool_contract_puzzle_hash.is_some() {
                return Err(Error::InvalidPoS);
            }
```

**File:** crates/chia-protocol/src/proof_of_space.rs (L539-539)
```rust
    #[case::v0_neither_lenient(0, false, false, 10, 10)]
```
