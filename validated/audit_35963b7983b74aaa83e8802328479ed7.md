### Title
Quadratic CPU exhaustion when completing Bitcoin transaction signatures with attacker-controlled input count - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`TransactionSignatureMachine::complete` processes the signature-share `Vec` of every participant once per transaction input, and inside that loop calls `Vec::remove(0)`, an O(inputs) memmove. The result is O(inputs² × participants) work per signing attempt. An unprivileged party can inflate `inputs` by sending many small outputs to a Serai Bitcoin address; those outputs are registered by `Scanner`, queued by `Scheduler`, and bundled into a `SignableTransaction` that all honest validators must then sign.

### Finding Description
In `complete`, the machine zips over every transaction input and, for each one, rebuilds a per-participant share map by iterating the full `shares` `HashMap` and calling `shares.remove(0)` on each participant's share vector:

```rust
// networks/bitcoin/src/wallet/send.rs:417-420
for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
  let sig = schnorr.complete(
    shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
  )?;
}
```

`shares` is a `Vec<SignatureShare>` of length equal to the input count (`TransactionSignMachine::sign` pushes one share per input, `send.rs:377-396`), and `Vec::remove(0)` shifts all remaining elements — O(inputs) per call. Combined with iterating `shares` (one entry per participant, each holding `inputs` shares) once per input, the completion step performs O(inputs² × participants) element moves, plus O(inputs × participants) `HashMap` construction per call.

This mirrors the reported bug class exactly: like `ExpandoObject::Add` being O(properties) inside an O(properties) deserialization loop, here `Vec::remove(0)` is O(inputs) inside an O(inputs) loop — an avoidable quadratic where `VecDeque::pop_front`, draining, or a per-input pre-split of shares would be linear.

The quadratic is also present, at a linear-in-inputs-per-participant level, in `sign` (`send.rs:364-371`), which builds a fresh `HashMap<Participant, _>` over all commitments for every input.

The input count is attacker-influenced: `SignableTransaction::new` only bounds the transaction by `MAX_STANDARD_TX_WEIGHT` (`send.rs:241`), permitting well over a thousand P2TR inputs, and each input corresponds to a `ReceivedOutput` an external sender created by paying to Serai's deposit address — data reachable purely through "Bitcoin transactions they send" per the threat model.

### Impact Explanation
A signing round for a transaction bundling ~1,700 attacker-created dust inputs performs ~1.7k × 1.7k × t share-element memmoves and HashMap builds inside `complete` on every validator's processor. Because signing sessions may be retried per attempt and multiple SignData/plans can accumulate, the CPU cost is burned repeatedly, degrading or stalling the processor's signing throughput. This is a CPU-exhaustion denial of service against the signing pipeline reachable solely with on-chain data any Bitcoin user can create — the same O(n²) "inordinate CPU effort" class as GHSA-92vj-hp7m-gwcj, rated Medium.

### Likelihood Explanation
Low-to-moderate. The attacker must pay real dust (≥546 sats per output) to create the inputs, so large amplification has an economic cost — but it requires no privilege, no validator status, and no collusion. Whether the scheduler actually bundles many attacker outputs into one transaction depends on `scheduler/utxo.rs` behavior; any batching path that aggregates queued UTXOs reaches the quadratic code.

### Recommendation
- Replace `shares.remove(0)` with a structure that supports O(1) front removal: convert each participant's share `Vec` into a `VecDeque` once, or split the `HashMap<Participant, Vec<SignatureShare>>` into per-input `HashMap`s upfront in a single O(inputs × participants) pass.
- Bound the input count in `SignableTransaction::new` (e.g., a constant such as the ~520-input figure noted in `transaction.rs:121`) rather than relying only on the weight limit.
- Optionally have the scheduler cap inputs per plan so dust consolidation happens across multiple smaller transactions.

### Proof of Concept
```rust
// Conceptual: networks/bitcoin/src/wallet/send.rs, TransactionSignatureMachine::complete
// Given a SignableTransaction with N inputs and t participants:

let mut shares: HashMap<Participant, Vec<SignatureShare<Secp256k1>>> = /* t entries, each len N */;

for (input, schnorr) in tx.input.iter_mut().zip(sigs.drain(..)) {      // N iterations
  let per_input: HashMap<_, _> = shares
    .iter_mut()                                                      // t iterations
    .map(|(l, shares)| (*l, shares.remove(0)))                       // O(N) memmove each
    .collect();
  // ...
}
// Total work: O(N^2 * t) element shifts + N HashMap builds of size t.
```
An attacker funds the vault address with ~1,700 dust outputs; when the scheduler produces a transaction spending them, every honest validator executing `complete` (and `sign`, which builds per-input commitment maps at `send.rs:364-371`) performs millions of unnecessary element moves per attempt.