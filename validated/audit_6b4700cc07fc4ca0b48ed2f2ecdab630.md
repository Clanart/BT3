### Title
Non-Strict Work Comparison Allows Operator to Pass Bridge Circuit with Equal Watchtower Work - (`File: circuits-lib/src/bridge_circuit/mod.rs`)

### Summary

The `bridge_circuit` function in `circuits-lib/src/bridge_circuit/mod.rs` enforces the work-dominance invariant with a non-strict less-than check (`total_work < max_total_work`), which passes when the operator's total work **equals** the watchtower's maximum total work. The protocol specification and module documentation both require the operator's work to be **strictly greater than** the watchtower's work. When the two values are equal, the operator's chain is not provably the canonical chain, yet the circuit accepts the proof and the operator avoids collateral slashing.

### Finding Description

The `bridge_circuit` function reads the operator's 128-bit truncated total work from the HCP and compares it against `max_total_work` derived from the highest-verified watchtower Work-Only Proof (WOP):

```rust
// circuits-lib/src/bridge_circuit/mod.rs, line 151-160
let total_work: TotalWork = input.hcp.chain_state.total_work[16..32]
    .try_into()
    .expect("Cannot fail");

// If total work is less than the max total work of watchtowers, panic
if total_work < max_total_work {
    panic!(
        "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
    );
}
```

The condition `total_work < max_total_work` panics only when the operator's work is **strictly less than** the watchtower's work, meaning the circuit silently accepts the case where `total_work == max_total_work`.

The protocol documentation is unambiguous. `docs/bridge-circuit.md` line 33 states: *"Asserts that the Operator's `total_work` from their HCP is **greater than** the `max_total_work` from the Watchtowers."* The module-level doc comment at line 19 repeats: *"Work Comparison: Asserts that the operator's claimed work is **greater than** the maximum work submitted by any valid watchtower."* The design rationale at line 65 explains why: *"The Operator must provide a HCP with **more work** compared to the WOP with maximum Work. This is necessary, since the canonical Bitcoin blockchain is determined by the total Work done."*

The `TotalWork` type is a 16-byte big-endian array derived by two independent truncation paths:

- **Operator HCP path** (`bridge_circuit`): `input.hcp.chain_state.total_work[16..32]` — the lower 16 bytes of the 32-byte chain state field.
- **Watchtower WOP path** (`work_only_circuit`): `work_conversion(total_work_u256)` which calls `work.into()` to split the U256 into `(U128, U128)` and returns the lower half as big-endian bytes.

Both paths extract the same lower-128-bit slice of the full 256-bit accumulated work, so the comparison is numerically consistent. The `TotalWork` struct derives `PartialOrd`/`Ord` from `[u8; 16]`, giving lexicographic (big-endian numeric) ordering, which is correct for this representation.

### Impact Explanation

Bitcoin's canonical chain is defined as the chain with the **most** accumulated proof-of-work. When `total_work == max_total_work`, the operator's chain and the watchtower's chain have identical accumulated work. This means:

1. The operator's chain is not provably the canonical chain — it is at best a competing fork of equal weight.
2. The operator's payout transaction may reside on a non-canonical fork.
3. The bridge circuit accepts the operator's proof, the operator receives reimbursement for a payout that may not be on the canonical chain, and the operator's collateral is not slashed.

This breaks the core bridge safety invariant: an operator who pays out on a non-canonical fork should be slashable. The allowed impact category is: *"Acceptance of a stale or otherwise invalid proof that changes withdrawal outcomes"* and *"Unauthorized state transition in payout flow that breaks bridge safety with material fund impact."*

### Likelihood Explanation

The 128-bit truncation makes a collision more feasible than with the full 256-bit value, but still requires two independently valid Bitcoin chains to accumulate exactly the same lower-128-bit work. On mainnet this is astronomically unlikely. On regtest or testnet4, where difficulty is minimal and block production is controlled, an operator could more plausibly engineer a chain whose truncated work matches a watchtower's WOP. The operator controls which HCP they submit and can mine regtest blocks freely, making this a realistic attack surface in test/staging deployments and a theoretical one on mainnet.

### Recommendation

Change the comparison from strict-less-than to less-than-or-equal, so the circuit panics whenever the operator's work does not **strictly exceed** the watchtower's maximum work:

```rust
// Before (incorrect — allows equal):
if total_work < max_total_work {
    panic!("Insufficient total work: ...");
}

// After (correct — requires strictly greater):
if total_work <= max_total_work {
    panic!("Insufficient total work: ...");
}
```

This aligns the code with the documented invariant and with the analogous fix described in the external report (changing `>=` to `>` in the swap invariant check).

### Proof of Concept

1. A watchtower submits a valid WOP for a Bitcoin chain with 128-bit truncated total work value `W`.
2. The operator constructs (or selects) a valid HCP for a **different fork** whose lower-128-bit total work is also exactly `W`. On regtest this is straightforward: mine blocks until the truncated work matches.
3. The operator submits a payout transaction on their fork and constructs a `BridgeCircuitInput` with this HCP.
4. Inside `bridge_circuit`, `total_work == max_total_work`, so `total_work < max_total_work` is `false` and the circuit does **not** panic.
5. The circuit produces a valid journal hash; the operator posts valid assert transactions; no challenger can disprove the circuit output because the circuit itself accepted the equal-work condition.
6. The operator receives reimbursement for a payout on a non-canonical fork. The watchtower's challenge is effectively neutralized despite the operator not having a strictly longer chain.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L19-20)
```rust
//! 4.  **Work Comparison:** Asserts that the operator's claimed work is greater than the
//!     maximum work submitted by any valid watchtower.
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L151-160)
```rust
    let total_work: TotalWork = input.hcp.chain_state.total_work[16..32]
        .try_into()
        .expect("Cannot fail");

    // If total work is less than the max total work of watchtowers, panic
    if total_work < max_total_work {
        panic!(
            "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
        );
    }
```

**File:** circuits-lib/src/work_only/mod.rs (L111-114)
```rust
fn work_conversion(work: U256) -> [u8; 16] {
    let (_, work): (U128, U128) = work.into();
    work.to_be_bytes()
}
```

**File:** circuits-lib/src/bridge_circuit/structs.rs (L95-115)
```rust
#[derive(Debug, Clone, Copy, PartialEq, PartialOrd, Eq, BorshDeserialize, BorshSerialize)]
pub struct TotalWork(pub [u8; 16]);

impl Deref for TotalWork {
    type Target = [u8; 16];

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl TryFrom<&[u8]> for TotalWork {
    type Error = &'static str;

    fn try_from(value: &[u8]) -> Result<Self, Self::Error> {
        let arr: [u8; 16] = value
            .try_into()
            .map_err(|_| "Expected 16 bytes for TotalWork")?;
        Ok(TotalWork(arr))
    }
}
```
