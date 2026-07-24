### Title
Incorrect Strict-Inequality in Bridge Circuit Total-Work Comparison Allows Equal-Work Fork Proof to Pass — (`circuits-lib/src/bridge_circuit/mod.rs`)

---

### Summary

The bridge circuit's canonical-chain safety check uses a strict-less-than (`<`) comparison instead of the required less-than-or-equal (`<=`). The protocol invariant, stated explicitly in both the code documentation and `docs/bridge-circuit.md`, is that the operator's total work must be **strictly greater than** the watchtower's maximum proven work. The current code allows the boundary value — equal total work — to pass, meaning an operator on an equal-work fork chain can generate a valid bridge circuit proof and claim reimbursement for a payout that was not made on the canonical chain.

---

### Finding Description

**Root cause — one wrong comparison operator:**

In `circuits-lib/src/bridge_circuit/mod.rs` at line 156:

```rust
// If total work is less than the max total work of watchtowers, panic
if total_work < max_total_work {          // ← should be <=
    panic!(
        "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
    );
}
``` [1](#0-0) 

The protocol invariant is documented in two places and is unambiguous:

- `docs/bridge-circuit.md` line 33: *"Asserts that the Operator's `total_work` from their HCP is **greater than** the `max_total_work` from the Watchtowers."*
- The function's own `# Panics` docstring (line 132): *"If `max_total_work` given by watchtowers is **greater than** `hcp.chain_state.total_work`."* [2](#0-1) 

**How the two values are produced:**

`total_work` is the lower 16 bytes (128 bits) of the operator's 32-byte big-endian `chain_state.total_work`:

```rust
let total_work: TotalWork = input.hcp.chain_state.total_work[16..32]
    .try_into()
    .expect("Cannot fail");
``` [3](#0-2) 

`max_total_work` is the 16-byte value produced by the Work-Only Circuit's `work_conversion`, which explicitly truncates the 256-bit total work to its lower 128 bits:

```rust
fn work_conversion(work: U256) -> [u8; 16] {
    let (_, work): (U128, U128) = work.into();
    work.to_be_bytes()
}
``` [4](#0-3) 

Both sides are therefore the lower 128 bits of their respective chains' total work. `TotalWork` derives `PartialOrd` over `[u8; 16]`, which is lexicographic — equivalent to big-endian numeric comparison. [5](#0-4) 

**The `ChainState.total_work` field is stored big-endian** (confirmed by `apply_block_headers` which writes `current_work.to_be_bytes()`): [6](#0-5) 

---

### Impact Explanation

The bridge circuit is the sole cryptographic gate that decides whether an operator's payout on a claimed Bitcoin chain is valid. If the circuit accepts a proof, the operator is entitled to reimbursement of bridged BTC from the bridge collateral.

When `total_work == max_total_work`, the operator's chain and the watchtower's chain have identical accumulated proof-of-work. Neither is definitively the canonical chain. The protocol requires the operator to prove their chain has **strictly more** work precisely to rule out this ambiguity. Accepting the equal-work case breaks the canonical-chain safety invariant:

- A malicious operator who paid out on a fork chain with total work W can generate a valid bridge circuit proof as long as no watchtower submits a Work-Only Proof for a chain with work **strictly greater than** W.
- If a watchtower submits a WOP for the canonical chain with work exactly equal to W (i.e., the fork and canonical chain have the same 128-bit truncated work), the check `total_work < max_total_work` evaluates to `false`, the circuit does not panic, and the operator's fraudulent proof is accepted.
- The operator then claims reimbursement for a payout that was not made on the canonical chain, resulting in loss of bridged BTC from the bridge.

**Severity: Medium.** The invariant is broken and the impact is loss of bridged BTC. The practical likelihood is low because two chains having exactly equal 128-bit truncated total work requires either a deliberate mining effort to produce an equal-work fork, or a natural collision (astronomically unlikely on mainnet). However, the protocol invariant is unambiguously violated, the fix is a single character change, and the consequence of exploitation is material fund loss.

---

### Likelihood Explanation

On Bitcoin mainnet, total work is currently ~2^93, well within the 128-bit range, so the lower 128 bits carry the full precision. For two chains to collide at exactly the same 128-bit total work, an attacker would need to mine a fork chain to exactly the same cumulative work as the canonical chain — requiring significant hash power and precise timing. This is not trivially achievable by an unprivileged attacker, but it is not cryptographically impossible. On testnet4 or regtest (where difficulty is low and work values are small), the collision space is much smaller and the attack is more feasible.

---

### Recommendation

Change the comparison from strict-less-than to less-than-or-equal:

```rust
// Before (incorrect — allows equal work to pass):
if total_work < max_total_work {

// After (correct — enforces strict greater-than invariant):
if total_work <= max_total_work {
```

This single-character fix aligns the implementation with the documented invariant and with the analogous check in the zkASM ecrecover fix referenced in the seed report.

---

### Proof of Concept

1. Watchtower submits a Work-Only Proof for the canonical chain with 128-bit total work value `W`.
2. Malicious operator constructs (or mines) a fork chain whose 128-bit truncated total work is also exactly `W`. The operator made a payout on this fork chain.
3. Operator generates a Header Chain Proof (HCP) for the fork chain; `chain_state.total_work[16..32]` encodes `W`.
4. Inside `bridge_circuit`:
   - `total_work = TotalWork(W)`
   - `max_total_work = TotalWork(W)` (from the watchtower's WOP)
   - `total_work < max_total_work` → `W < W` → `false` → **no panic**
5. The bridge circuit proof is accepted. The operator claims reimbursement for a payout on a non-canonical fork chain, draining bridged BTC from the bridge.

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L129-136)
```rust
/// # Panics
///
/// - If the method ID in `hcp` does not match `HEADER_CHAIN_METHOD_ID`.
/// - If `max_total_work` given by watchtowers is greater than `hcp.chain_state.total_work`.
/// - If the SPV proof is invalid.
/// - If the storage proof verification fails.
/// - If the block hash of the light client proof does not match the payout transaction's block hash.
/// - If the withdrawal transaction ID does not match the referenced input in `payout_spv`.
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L151-153)
```rust
    let total_work: TotalWork = input.hcp.chain_state.total_work[16..32]
        .try_into()
        .expect("Cannot fail");
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

**File:** circuits-lib/src/work_only/mod.rs (L111-114)
```rust
fn work_conversion(work: U256) -> [u8; 16] {
    let (_, work): (U128, U128) = work.into();
    work.to_be_bytes()
}
```

**File:** circuits-lib/src/bridge_circuit/structs.rs (L95-96)
```rust
#[derive(Debug, Clone, Copy, PartialEq, PartialOrd, Eq, BorshDeserialize, BorshSerialize)]
pub struct TotalWork(pub [u8; 16]);
```

**File:** circuits-lib/src/header_chain/mod.rs (L509-509)
```rust
        self.total_work = current_work.to_be_bytes();
```
