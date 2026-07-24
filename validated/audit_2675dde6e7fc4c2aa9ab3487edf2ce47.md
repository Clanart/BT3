### Title
Off-by-One in Work Comparison Allows Operator with Equal Work to Pass Bridge Circuit — (`circuits-lib/src/bridge_circuit/mod.rs`)

---

### Summary

The `bridge_circuit` function uses a strict `<` comparison when checking the operator's total work against the watchtower's maximum total work. The specification and documentation both require the operator's work to be **strictly greater than** the watchtower's max work, but the implementation allows the equality case (`operator_work == watchtower_max_work`) to pass silently. This is the direct Clementine analog of the BPF boundary-condition bug: the equality case is not handled, causing the wrong outcome.

---

### Finding Description

In `bridge_circuit`, after computing `max_total_work` from watchtower challenges and extracting the operator's `total_work` from their Header Chain Proof (HCP), the circuit enforces:

```rust
// If total work is less than the max total work of watchtowers, panic
if total_work < max_total_work {
    panic!(
        "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
    );
}
``` [1](#0-0) 

The specification, stated in both the module-level doc comment and the bridge circuit documentation, is unambiguous:

> "Asserts that the Operator's `total_work` from their HCP is **greater than** the `max_total_work` from the Watchtowers." [2](#0-1) [3](#0-2) 

The `docs/bridge-circuit.md` also states:

> "The Operator must provide a HCP with **more work** compared to the WOP with maximum Work." [4](#0-3) 

The `<` operator only panics when `total_work` is strictly less than `max_total_work`. When `total_work == max_total_work`, the condition is `false`, no panic occurs, and the operator's proof is accepted — contradicting the specification.

---

### Impact Explanation

The bridge circuit is the core security gate for operator withdrawals. Its purpose is to ensure the operator's claimed Bitcoin chain has **more** cumulative proof-of-work than any challenging watchtower, thereby proving the operator is on the canonical chain.

When `operator_total_work == watchtower_max_total_work`, the operator is **not** on a chain with strictly more work than the watchtower's chain. By Bitcoin's longest-chain rule, equal work does not establish canonical-chain superiority. An operator on a fork with exactly equal accumulated work (as represented in the 128-bit truncated `TotalWork` value) would pass the circuit check and could successfully generate a valid bridge proof, enabling them to claim reimbursement from the bridge vault for a payout that was not on the canonical chain.

The `TotalWork` type is a 128-bit truncation of the full 256-bit work value (lower 128 bits): [5](#0-4) [6](#0-5) 

Truncation to 128 bits increases the probability of collision compared to the full 256-bit value, making the equality case more reachable than it would otherwise be.

---

### Likelihood Explanation

The `total_work` values are 128-bit big-endian byte arrays derived from Bitcoin block headers. While exact equality of two independently-computed 128-bit work values is unlikely under normal conditions, the following factors increase the practical risk:

1. **Truncation amplifies collision probability**: The lower 128 bits of two 256-bit work values are more likely to collide than the full 256-bit values.
2. **Attacker control**: A malicious operator with sufficient mining resources can craft a fork chain whose accumulated work, after 128-bit truncation, equals the watchtower's reported work. The operator controls which HCP they submit and can select a chain tip that produces the desired `total_work[16..32]` value.
3. **No defense in depth**: The circuit has no secondary check; passing this single comparison is sufficient to proceed to SPV and storage proof verification, which the operator can satisfy legitimately.

---

### Recommendation

Change the comparison from strict less-than to less-than-or-equal, so that the equality case is also rejected:

```diff
-    if total_work < max_total_work {
+    if total_work <= max_total_work {
         panic!(
             "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
         );
     }
``` [1](#0-0) 

This aligns the implementation with the specification: the operator's work must be **strictly greater than** the watchtower's maximum work.

---

### Proof of Concept

1. A watchtower submits a valid Work Only Proof (WOP) with `total_work = W` (128-bit value).
2. A malicious operator constructs or selects a fork chain whose HCP produces `total_work[16..32] = W` (same 128-bit value after truncation).
3. The operator submits their bridge circuit input with this HCP.
4. At line 156, `total_work < max_total_work` evaluates to `W < W` → `false`. No panic.
5. The circuit proceeds to SPV, LCP, and storage proof verification, all of which the operator can satisfy for their fork chain.
6. The circuit commits a valid `journal_hash`, producing a valid Groth16 proof.
7. The operator uses this proof to claim reimbursement from the bridge vault for a withdrawal that was not confirmed on the canonical Bitcoin chain. [7](#0-6)

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L19-20)
```rust
//! 4.  **Work Comparison:** Asserts that the operator's claimed work is greater than the
//!     maximum work submitted by any valid watchtower.
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L148-160)
```rust
    let (max_total_work, challenge_sending_watchtowers) =
        total_work_and_watchtower_flags(&input, &work_only_image_id);

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

**File:** docs/bridge-circuit.md (L33-33)
```markdown
    * Asserts that the Operator's `total_work` from their HCP is greater than the `max_total_work` from the Watchtowers.
```

**File:** docs/bridge-circuit.md (L65-65)
```markdown
    provided by the Watchtowers. The Operator must provide a HCP with more work compared to the WOP with maximum Work. This is necessary, since the canonical Bitcoin blockchain is determined by the total Work done. If the Operator fails to do so, this automatically means that the Operator did not follow the canonical chain; therefore, is already malicious.
```

**File:** circuits-lib/src/work_only/mod.rs (L111-113)
```rust
fn work_conversion(work: U256) -> [u8; 16] {
    let (_, work): (U128, U128) = work.into();
    work.to_be_bytes()
```
