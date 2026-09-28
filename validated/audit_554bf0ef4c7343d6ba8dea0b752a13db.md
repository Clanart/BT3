### Title
`TransactionSignMachine::sign` panics on out-of-bounds index when a participant sends a short preprocess vector - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes a ported library (`TickMath`) that was designed around pre-Solidity-0.8 wrapping semantics and therefore reverts (panics) on inputs the original code handled — a reachable abort on a core execution path. The Serai analog is an arithmetic/indexing panic reachable purely from untrusted bytes supplied by a counterparty: `TransactionSignMachine::sign` indexes each participant's preprocess `Vec` by input index without checking its length, so any signer who supplies a `Preprocess` vector shorter than the transaction's input count causes an out-of-bounds panic that aborts threshold signing for the whole group.

### Finding Description
`SignableTransaction::multisig` builds one `AlgorithmMachine` per transaction input, so `TransactionSignMachine` holds `sigs.len() == tx.input.len()` inner machines. In `sign`, the code re-slices the `HashMap<Participant, Vec<Preprocess>>` of received preprocesses into per-input maps by positional indexing:

```rust
// networks/bitcoin/src/wallet/send.rs:364-371
let commitments = (0 .. self.sigs.len())
  .map(|c| {
    commitments
      .iter()
      .map(|(l, commitments)| (*l, commitments[c].clone()))
      .collect::<HashMap<_, _>>()
  })
  .collect::<Vec<_>>();
```

The `Vec<Preprocess>` for each participant is produced by `TransactionSignMachine::read_preprocess` (`send.rs:351-353`), which reads a per-signer `Vec<Preprocess>` straight from the wire via `sig.read_preprocess(reader)` with no constraint tying its length to `self.sigs.len()`. A malicious participant (or any party relaying a tampered preprocess blob) can therefore supply a preprocess vector of length `< tx.input.len()`. When `c` reaches that length, `commitments[c]` panics with an index-out-of-bounds abort.

Like the `TickMath` case, this is a routine that assumes an invariant (every preprocess vector is at least as long as the input count) that is not enforced at deserialization, and its violation manifests as an unhandled panic rather than a `FrostError`, killing the signing session on the execution path `TransactionSignMachine::sign` → `AlgorithmSignMachine::sign`.

The same class appears in `TransactionSignatureMachine::complete` (`send.rs:417-420`), which calls `shares.remove(0)` on each participant's `Vec<SignatureShare>` once per input; `read_share` (`send.rs:409-411`) likewise does not bound the vector length, so a short share vector panics at completion time after all signing rounds have already run.

### Impact Explanation
Any single participant in a Bitcoin FROST signing session can deterministically crash every honest signer's `sign`/`complete` call for a multi-input transaction, indefinitely halting spends from the threshold wallet (the protocol's core function — producing signed Bitcoin transactions). Because the panic is unconditional once reached (indexing past the end of an attacker-controlled `Vec`), the malicious signer can trigger it on every attempt, not probabilistically. This mirrors the external report's impact: core functions becoming unexecutable due to a reachable abort in shared code.

### Likelihood Explanation
The trigger is trivial: the attacker's `read_preprocess`/`read_share` serialization simply omits entries. No cryptographic work, timing, or collusion is needed; it is a deterministic panic driven entirely by bytes the attacker controls. The only mitigating factor is that a well-behaved honest coordinator that round-trips `Preprocess` vectors it created itself will not hit it — the vulnerability requires a faulty/malicious signer in the set, which is precisely the threat model FROST signing must tolerate.

### Recommendation
Validate lengths before indexing. In `TransactionSignMachine::sign`, check `commitments.values().all(|v| v.len() == self.sigs.len())` (and reject/error with a `FrostError` identifying the faulty participant rather than indexing blindly). In `TransactionSignatureMachine::complete`, verify each `shares` vector has exactly `self.tx.input.len()` elements before draining. Preferably, enforce the invariant at `read_preprocess`/`read_share` by reading exactly `self.sigs.len()` items per participant (as `self.sigs.iter().map(|sig| sig.read_preprocess(reader))` already implies per-signer structure — the outer `Vec` framing should be dropped or its length asserted).

### Proof of Concept
1. Construct a `SignableTransaction` with 2 `ReceivedOutput` inputs (e.g. via `SignableTransaction::new` with two inputs, one payment, and a change script).
2. Build the `TransactionMachine` via `multisig(keys)` and run `preprocess(rng)` for each of `t` participants.
3. For the malicious participant, serialize its `Vec<Preprocess>` then truncate it to a single entry, or craft bytes so `read_preprocess` yields a `Vec` of length 1.
4. Feed the honest node's `TransactionSignMachine::sign` a `HashMap` containing that short vector: at `c == 1`, `commitments[c]` panics with `index out of bounds`, aborting the signing session. The analogous `complete` path panics on `shares.remove(0)` when the share vector is empty.