### Title
Off-by-one boundary check in `total_work` vs `max_total_work` comparison allows equal (not strictly greater) operator proof-of-work to pass Bridge Circuit verification - (File: `circuits-lib/src/bridge_circuit/mod.rs`)

### Summary
The Bridge Circuit's proof-of-work sufficiency check compares the operator's `total_work` (from their Header Chain Proof) against `max_total_work` (the highest work proven by a valid watchtower challenge) using a strict "less-than" rejection test, `if total_work < max_total_work { panic!(...) }`. This is the same class of boundary error as the referenced Arrakis finding: the code's module-level and function-level documentation explicitly requires the operator's work to be **strictly greater than** the watchtower's proven work, but the implemented comparison only rejects the strictly-less case, silently accepting the equality case that the documented invariant forbids.

### Finding Description
`circuits-lib/src/bridge_circuit/mod.rs` documents the intended invariant in three places:
- Module doc: "the operator has more cumulative proof-of-work than any challenging watchtower" [1](#0-0) 
- Function doc: "Asserts that the operator's claimed work is greater than the maximum work submitted by any valid watchtower" and "If `max_total_work` given by watchtowers is greater than `hcp.chain_state.total_work`" (panic condition) [2](#0-1) 
- `docs/bridge-circuit.md`: "Asserts that the Operator's `total_work` from their HCP is greater than the `max_total_work` from the Watchtowers." [3](#0-2) 

However, the actual implementation only panics when `total_work` is strictly less than `max_total_work`, letting `total_work == max_total_work` pass silently: [4](#0-3) 

This is corroborated by the protocol-level design intent, which explicitly builds in a safety margin so that the operator's committed work is always meant to be *definitely higher*, never merely equal, than any watchtower's proven work: [5](#0-4) 

The mismatch matters because equality between the operator's HCP `total_work` and a watchtower's WOP `total_work` is not a coincidental, astronomically-unlikely tie (as it might first appear) — it is the deterministic outcome whenever both the operator and the watchtower report a Header Chain Proof / Work-Only Proof anchored at the exact same real Bitcoin chain height. In that scenario, both values are computed identically from real chain data, so an equal-work condition is trivially reachable by an operator who does not wait for the intended extra confirmation buffer before finalizing their claim, or who otherwise submits an HCP tied to the same tip a watchtower already proved. The code's failure to enforce strict inequality means the circuit accepts an operator proof that has not been shown to extend the canonical chain *beyond* the point a challenging watchtower already proved, contrary to the documented invariant meant to establish which claim is canonical.

### Impact Explanation
This breaks the intended equality binding: `operator_total_work > max_watchtower_total_work` (the condition the circuit's Groth16 proof is supposed to enforce as a public commitment used for on-chain disprove/no-disprove decisions) is weakened to `operator_total_work >= max_watchtower_total_work`. Under the "false circuit claim proved" impact category, this means the Bridge Circuit can produce a valid proof (and thus a valid journal/commitment consumed by `ClementineDisproveScript`/`BridgeDisproveScript`) for an operator whose claimed chain state does not actually satisfy the documented "operator chain is heavier than any watchtower-proven chain" requirement — undermining the guarantee that a successfully-challenged operator's claim is provably canonical before the operator's execution is treated as valid.

### Likelihood Explanation
Reaching the equal-work boundary does not require any cryptographic coincidence: it occurs whenever the operator's HCP and the highest-verified watchtower WOP happen to be anchored at the same real Bitcoin chain tip, which is a realistic occurrence if an operator submits their claim without waiting the buffer period the state-machine design assumes (`kickoff.rs` comment on `TimeToSendLatestBlockhash`). This makes the boundary condition practically reachable by an operator controlling the timing of their own claim, rather than a purely theoretical, unreachable edge case.

### Recommendation
Change the check in `circuits-lib/src/bridge_circuit/mod.rs` from `if total_work < max_total_work` to `if total_work <= max_total_work`, so the circuit enforces the documented strict inequality (`total_work > max_total_work`) rather than allowing ties to pass.

### Proof of Concept
1. A watchtower submits a valid watchtower-challenge transaction with a Work-Only Proof (WOP) whose `total_work` corresponds to real Bitcoin chain height `H`.
2. The operator, instead of waiting for the additional confirmation buffer (`time_to_send_watchtower_challenge`-style delay) intended to guarantee their own Header Chain Proof (HCP) exceeds any challenger's work, submits (or already possesses) an HCP whose `chain_state.total_work` is also anchored at height `H` (identical value, since both are deterministic functions of the same real chain data).
3. In `bridge_circuit()`, `total_work == max_total_work`, so `total_work < max_total_work` evaluates to `false` and no panic occurs, contrary to the documented requirement that the operator's work be strictly greater. [6](#0-5) 
4. The circuit proceeds to generate a valid proof/journal for the operator's claim despite not having demonstrated work strictly beyond the watchtower's proven challenge.

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L6-8)
```rust
//! for a valid peg-out request for an existing peg-in transaction. The circuit
//! ensures that an operator's claimed Bitcoin chain state is valid and that it has more
//! cumulative proof-of-work than any challenging watchtower.
```

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

**File:** docs/bridge-circuit.md (L33-33)
```markdown
    * Asserts that the Operator's `total_work` from their HCP is greater than the `max_total_work` from the Watchtowers.
```

**File:** core/src/states/kickoff.rs (L57-61)
```rust
    /// Vvent that is used to indicate that it is time for the owner to send latest blockhash tx.
    /// Matcher for this event is created after all watchtower challenge utxos are spent.
    /// Latest blockhash is sent some blocks after all watchtower challenge utxos are spent, so that the total work until the block commiitted
    /// in latest blockhash is definitely higher than the highest work in valid watchtower challenges.
    TimeToSendLatestBlockhash,
```
