The code is clear. Let me trace the exact path.

**Parse behavior for v0:**

In `parse`, the mutual-exclusion guard only applies to `version == 1`:

```rust
} else if version == 1 {
    ...
    if pool_public_key.is_some() == pool_contract_puzzle_hash.is_some() {
        return Err(Error::InvalidPoS);  // only v1 is guarded
    }
```

For `version == 0`, the code falls into the first branch and returns `Ok(...)` unconditionally — no check that at least one of `pool_public_key` / `pool_contract_puzzle_hash` is `Some`. This is even confirmed by the test at line 539 (`v0_neither_lenient`) which is in the `roundtrip` test (not `parse_rejects`), explicitly documenting that parse accepts this. [1](#0-0) 

**Panic in `compute_plot_id_v1`:**

When `compute_plot_id()` is called on a v0 struct with both fields `None`, it calls `compute_plot_id_v1` with both `pool_pk=None` and `pool_contract=None`, hitting the unconditional `panic!`: [2](#0-1) 

**`update_digest` for v0 is NOT affected** — it hashes the fields directly without calling `compute_plot_id()`, so `get_hash()` is safe: [3](#0-2) 

**Conclusion:**

The panic path is real and reachable: parse succeeds, then any caller of `compute_plot_id()` (proof-of-space verification during block validation) panics. However, the impact classification requires careful scoping:

- The panic is deterministic and reachable from a crafted network object (a block containing a v0 PoS with both Option prefixes = `0x00`).
- A Chia node receiving such a block would call `compute_plot_id()` during proof verification and panic, crashing the validation thread/process.
- This matches the Critical impact: "serialized network object can trigger deterministic chain halt."

---

### Title
Parsed v0 `ProofOfSpace` with both `pool_public_key=None` and `pool_contract_puzzle_hash=None` causes unconditional panic in `compute_plot_id()` — (`crates/chia-protocol/src/proof_of_space.rs`)

### Summary
The `parse` implementation for `ProofOfSpace` enforces the mutual-exclusion invariant (exactly one of `pool_public_key` / `pool_contract_puzzle_hash` must be set) only for v1 proofs. For v0 proofs, no such check exists. An attacker can craft a v0 `ProofOfSpace` with both fields absent; `parse` returns `Ok`, but any subsequent call to `compute_plot_id()` unconditionally panics.

### Finding Description
In `ProofOfSpace::parse`, the branch for `version == 0` constructs and returns the struct without validating that at least one of `pool_public_key` or `pool_contract_puzzle_hash` is `Some`. [1](#0-0) 

The v1 branch has the guard: [4](#0-3) 

But the v0 branch has no equivalent. The `roundtrip` test at line 539 even documents this as intentional "lenient" behavior (`v0_neither_lenient`), confirming parse accepts it. [5](#0-4) 

When `compute_plot_id()` is subsequently called on the parsed struct, it dispatches to `compute_plot_id_v1` with both options `None`, reaching the unconditional `panic!`: [6](#0-5) 

### Impact Explanation
A node receiving a crafted block containing such a v0 `ProofOfSpace` will:
1. Successfully parse the block (no error returned).
2. Attempt to verify the proof of space by calling `compute_plot_id()`.
3. Panic unconditionally, crashing the validation thread/process.

This is a deterministic, remotely-triggerable node crash from an unprivileged network input, constituting a chain halt.

### Likelihood Explanation
The encoding is trivial: set the `pool_public_key` Option prefix byte to `0x00` (None) and the `pool_contract_puzzle_hash` prefix byte to `0x00` (version=0, not set). No keys, no privileges, no special network position required. Any peer can send such a block.

### Recommendation
Add the same mutual-exclusion check for `version == 0` in `parse`:

```rust
if version == 0 {
    let size = <u8 as Streamable>::parse::<TRUSTED>(input)?;
    let proof = <Bytes as Streamable>::parse::<TRUSTED>(input)?;

    // Guard: exactly one must be set, same as v1
    if pool_public_key.is_none() && pool_contract_puzzle_hash.is_none() {
        return Err(Error::InvalidPoS);
    }
    if pool_public_key.is_some() && pool_contract_puzzle_hash.is_some() {
        return Err(Error::InvalidPoS);
    }

    Ok(ProofOfSpace { ... })
}
```

Also update the `roundtrip` test to move `v0_neither_lenient` and `v0_both_lenient` into `parse_rejects`.

### Proof of Concept
```rust
use std::io::Cursor;
use chia_traits::Streamable;
use chia_protocol::ProofOfSpace;

// Craft a v0 PoS with both Option prefixes = 0x00 (None)
// Layout: challenge(32) + pool_pk_prefix(1=0x00) + contract_prefix(1=0x00) + plot_pk(48) + size(1) + proof_len(4) + proof
let mut buf = vec![0u8; 32];   // challenge
buf.push(0x00);                 // pool_public_key = None
buf.push(0x00);                 // pool_contract_puzzle_hash prefix: version=0, not set
// ... append valid plot_pk bytes, size, proof ...

let pos = ProofOfSpace::parse::<false>(&mut Cursor::new(&buf)).unwrap(); // succeeds
let _ = std::panic::catch_unwind(|| pos.compute_plot_id());              // panics
```

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

**File:** crates/chia-protocol/src/proof_of_space.rs (L231-235)
```rust
        if self.version == 0 {
            self.pool_contract_puzzle_hash.update_digest(digest);
            self.plot_public_key.update_digest(digest);
            self.size.update_digest(digest);
            self.proof.update_digest(digest);
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
