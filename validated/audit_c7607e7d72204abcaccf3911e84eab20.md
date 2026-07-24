### Title
Off-by-one in work comparison allows operator to pass bridge circuit when watchtower work equals operator work — (`circuits-lib/src/bridge_circuit/mod.rs`)

---

### Summary

The bridge circuit's work-sufficiency check uses a strict-less-than (`<`) comparison instead of less-than-or-equal (`<=`). The module's own documentation states the operator's work must be **strictly greater than** the maximum watchtower work, but the code allows the operator to pass when the two values are **equal**, violating the protocol invariant.

---

### Finding Description

In `bridge_circuit()`, after computing `max_total_work` from all valid watchtower challenges, the circuit checks:

```rust
// circuits-lib/src/bridge_circuit/mod.rs, lines 151-160
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

The module-level documentation at line 20 explicitly states the invariant:

> **Work Comparison:** Asserts that the operator's claimed work is **greater than** the maximum work submitted by any valid watchtower.

The condition `total_work < max_total_work` only panics when the operator's work is **strictly less** than the watchtower's. When `total_work == max_total_work`, the circuit does **not** panic and the operator's proof is accepted. The correct guard is `total_work <= max_total_work`.

This is structurally identical to the external report's bug: a boundary value (`cumProb >= rarityRank` vs `cumProb > rarityRank`) is included on the wrong side of the comparison, causing the invariant to be violated at exactly the boundary.

The `TotalWork` type is a `[u8; 16]` big-endian slice of the lower 128 bits of the 32-byte cumulative chain work:

```rust
// circuits-lib/src/bridge_circuit/structs.rs, lines 96-104
#[derive(Debug, Clone, Copy, PartialEq, PartialOrd, Eq, BorshDeserialize, BorshSerialize)]
pub struct TotalWork(pub [u8; 16]);
```

Its `PartialOrd` is lexicographic over the big-endian bytes, which is numerically correct. The bug is solely in the comparison operator used in the guard.

---

### Impact Explanation

The bridge circuit is the ZK proof that authorises an operator's payout transaction. Its security guarantee is: *the operator's Bitcoin chain has more accumulated proof-of-work than any competing chain submitted by a watchtower*. This is the mechanism that ensures the operator fronted funds on the canonical chain.

When `total_work == max_total_work`, two chains have identical accumulated work. Neither is definitively canonical. The protocol must reject the operator's claim in this case (the watchtower challenge should succeed), but the circuit accepts it. An operator could therefore claim a payout reimbursement for a payout made on a non-canonical chain of equal weight, causing the bridge to reimburse an operator who did not actually front funds on the canonical chain — a direct loss of bridged BTC from the bridge's collateral.

---

### Likelihood Explanation

- **Regtest / signet / testnet4**: Work per block is fixed at minimum difficulty. Total work is simply `num_blocks × work_per_block`. A watchtower that mines exactly as many blocks as the operator produces an exact tie. This is a realistic and easily reproducible scenario in test environments and during protocol development.
- **Mainnet**: Exact 128-bit equality of accumulated work is astronomically unlikely for independently mined chains, making exploitation practically infeasible in production. However, the invariant is still broken and the circuit's correctness guarantee is formally unsound.

---

### Recommendation

Change the comparison from strict-less-than to less-than-or-equal:

```rust
// circuits-lib/src/bridge_circuit/mod.rs
// Before:
if total_work < max_total_work {

// After:
if total_work <= max_total_work {
```

This enforces the documented invariant that the operator's work must be **strictly greater than** the maximum watchtower work.

---

### Proof of Concept

Consider a regtest scenario where each block contributes exactly `W` units of work:

1. Operator mines `N` blocks → `total_work = N × W`.
2. A watchtower also mines `N` blocks on a competing fork → `max_total_work = N × W`.
3. The watchtower submits a valid work-only Groth16 proof encoding `total_work = N × W`.
4. Inside `bridge_circuit()`:
   - `total_work = TotalWork([N×W bytes])` (from operator's HCP)
   - `max_total_work = TotalWork([N×W bytes])` (from watchtower's proof)
   - `total_work < max_total_work` evaluates to `false` (they are equal)
   - The circuit does **not** panic; the operator's proof is accepted.
5. The operator receives reimbursement for a payout on a chain that is not definitively canonical, while the watchtower's legitimate challenge is silently ignored.

The root cause is at: [1](#0-0) 

The documented invariant requiring strict inequality is at: [2](#0-1) 

The `TotalWork` type whose `PartialOrd` governs the comparison is at: [3](#0-2)

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L19-20)
```rust
//! 4.  **Work Comparison:** Asserts that the operator's claimed work is greater than the
//!     maximum work submitted by any valid watchtower.
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L155-160)
```rust
    // If total work is less than the max total work of watchtowers, panic
    if total_work < max_total_work {
        panic!(
            "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
        );
    }
```

**File:** circuits-lib/src/bridge_circuit/structs.rs (L96-104)
```rust
pub struct TotalWork(pub [u8; 16]);

impl Deref for TotalWork {
    type Target = [u8; 16];

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}
```
