I have found the analog. The key is in the bridge circuit's work comparison at line 156 of `circuits-lib/src/bridge_circuit/mod.rs`. Let me verify the exact code and the protocol documentation's stated requirement.

### Title
Bridge Circuit Accepts Equal Work as Sufficient — Operator on Equal-Work Fork Bypasses Canonical-Chain Proof (`circuits-lib/src/bridge_circuit/mod.rs`)

---

### Summary

The `bridge_circuit` function uses a strict `<` comparison to check whether the operator's total work exceeds the watchtower's maximum total work. The protocol specification explicitly requires the operator's work to be **strictly greater than** the watchtower's work. When both values are equal, the check passes silently, allowing an operator whose chain has the same truncated 128-bit cumulative work as the canonical chain to produce a valid bridge proof — bypassing the canonical-chain safety invariant and enabling reimbursement for a payout on a non-canonical fork.

---

### Finding Description

In `bridge_circuit`, after computing `max_total_work` from all valid watchtower challenges and extracting the operator's own `total_work` from their Header Chain Proof (HCP), the guard is:

```rust
// circuits-lib/src/bridge_circuit/mod.rs, line 155-160
// If total work is less than the max total work of watchtowers, panic
if total_work < max_total_work {
    panic!(
        "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
    );
}
``` [1](#0-0) 

The condition `total_work < max_total_work` only panics when the operator's work is **strictly less than** the watchtower's work. When they are **equal**, the condition is `false` and execution continues normally — the circuit succeeds.

The protocol documentation states the requirement unambiguously in two places:

> "Asserts that the Operator's `total_work` from their HCP is **greater than** the `max_total_work` from the Watchtowers." [2](#0-1) 

> "The Operator must provide a HCP with **more** work compared to the WOP with maximum Work. This is necessary, since the canonical Bitcoin blockchain is determined by the total Work done. If the Operator fails to do so, this automatically means that the Operator did not follow the canonical chain; therefore, is already malicious." [3](#0-2) 

Both values being compared are 16-byte (128-bit) truncations of the full 256-bit total work. The operator's value is sliced from `input.hcp.chain_state.total_work[16..32]`: [4](#0-3) 

The watchtower's value is produced by the work-only circuit, which explicitly truncates the 256-bit work to its lower 128 bits via `work_conversion`: [5](#0-4) 

This truncation means two chains with different actual 256-bit total work can produce identical 128-bit `total_work` values, making the equality case more reachable than it would be with a full 256-bit comparison.

---

### Impact Explanation

When `total_work == max_total_work`, the bridge circuit completes successfully and commits a valid `journal_hash`. This journal hash is the output used by the BitVM disprove mechanism to determine whether the operator's assertion is correct. A passing circuit means:

1. No verifier can disprove the operator's assertion.
2. The operator collects reimbursement from the `MoveToVault` UTXO (the full bridge deposit amount — 1 BTC in the standard paramset).
3. The operator's collateral is not slashed.

If the operator paid out on a non-canonical fork (i.e., a chain that is not the longest-work chain), the bridge's safety invariant is broken: the operator is reimbursed for a payout that does not correspond to a valid canonical-chain withdrawal, effectively stealing bridged BTC. [6](#0-5) 

---

### Likelihood Explanation

The attack requires the operator's fork chain to have a truncated 128-bit total work value exactly equal to the canonical chain's truncated 128-bit total work as proven by the highest-work watchtower. This is:

- **Unlikely under normal conditions**: Bitcoin's proof-of-work makes exact 128-bit equality between two independently mined chains rare.
- **More feasible under a targeted attack**: An attacker with significant hash power mining a competing fork can target a specific truncated work value. The 128-bit truncation (rather than 256-bit) reduces the search space and makes grinding to a specific value more practical than it would otherwise be.
- **Amplified by the truncation design**: The `work_conversion` in the work-only circuit intentionally discards the upper 128 bits of total work. Two chains whose full 256-bit work differs only in the upper 128 bits will produce identical `max_total_work` and `total_work` values, making the equality case reachable without any grinding at all if the upper bits happen to differ. [5](#0-4) 

---

### Recommendation

Change the comparison from strict `<` to `<=` so that equal work is also rejected:

```rust
// Before (incorrect):
if total_work < max_total_work {

// After (correct):
if total_work <= max_total_work {
```

This enforces the protocol invariant that the operator must have **strictly more** cumulative work than any challenging watchtower, matching both the specification in `docs/bridge-circuit.md` and the security rationale that equal work does not prove canonical-chain membership. [1](#0-0) 

---

### Proof of Concept

1. A watchtower observes an operator paying out on a fork and submits a valid Work-Only Proof (WOP) for the canonical chain. The WOP's `total_work` (128-bit truncated) is `W`.
2. The operator constructs an HCP for their fork chain such that `chain_state.total_work[16..32]` also equals `W`. This is possible if:
   - The fork chain's lower 128 bits of total work happen to equal the canonical chain's lower 128 bits (no grinding needed if upper bits differ), **or**
   - The attacker grinds the fork chain's work to match the lower 128 bits of `W`.
3. The operator submits their bridge circuit proof. The check `total_work < max_total_work` evaluates as `W < W` → `false`. The circuit does not panic.
4. The circuit commits a valid `journal_hash`. No verifier can produce a valid disprove transaction.
5. The operator claims reimbursement via the `Reimburse` transaction, receiving the full bridge deposit amount from the `MoveToVault` UTXO, despite having paid out on a non-canonical chain. [7](#0-6) [8](#0-7)

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L137-160)
```rust
pub fn bridge_circuit(guest: &impl ZkvmGuest, work_only_image_id: [u8; 32]) {
    let input: BridgeCircuitInput = guest.read_from_host();
    assert_eq!(
        HEADER_CHAIN_METHOD_ID, input.hcp.method_id,
        "Invalid method ID for header chain circuit: expected {:?}, got {:?}",
        HEADER_CHAIN_METHOD_ID, input.hcp.method_id
    );

    // Verify the HCP
    guest.verify(input.hcp.method_id, &input.hcp);

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

**File:** docs/bridge-circuit.md (L63-65)
```markdown
    For each Watchtower, the signature that is for spending the connector UTXO for the challenge-sending transaction is verified. If the signature is verified, the corresponding bit flag to that Watchtower will be set to 1.
    Then the `Work`s provided by the Watchtowers are sorted in a descending order. Then, until the first Groth16 proof is verified, they are looped. This way, the Operator obtains the maximum valid amount of Work
    provided by the Watchtowers. The Operator must provide a HCP with more work compared to the WOP with maximum Work. This is necessary, since the canonical Bitcoin blockchain is determined by the total Work done. If the Operator fails to do so, this automatically means that the Operator did not follow the canonical chain; therefore, is already malicious.
```

**File:** circuits-lib/src/work_only/mod.rs (L83-90)
```rust
    let total_work_u256: U256 =
        U256::from_be_bytes(input.header_chain_circuit_output.chain_state.total_work);
    let words = work_conversion(total_work_u256);
    // Due to the nature of borsh serialization, this will use little endian bytes in the items it serializes/deserializes
    guest.commit(&WorkOnlyCircuitOutput {
        work_u128: words,
        genesis_state_hash: input.header_chain_circuit_output.genesis_state_hash,
    });
```
