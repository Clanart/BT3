### Title
ReceivedOutput deserialization accepts an arbitrary scalar offset without verifying it matches the output's script_pubkey — untrusted bytes can inject mismatched spend metadata - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to the referenced SQL injection (a JSON `property` value deserialized and used unsanitized in a sensitive context), `ReceivedOutput::read` deserializes a scalar `offset` and a `TxOut`/`OutPoint` independently, with no verification that `output.script_pubkey` is actually the P2TR script derived from `key + offset·G`. The correct binding is only ever established inside `Scanner::scan_transaction`, which constructs `ReceivedOutput` from its internal `scripts` map. Any `ReceivedOutput` obtained via `read`/`serialize` (a listed untrusted-bytes entry point) bypasses that binding entirely.

### Finding Description
`Scanner::register_offset`/`scan_transaction` build `ReceivedOutput`s where `offset` is cryptographically bound to `output.script_pubkey` via `p2tr_script_buf(self.key + GENERATOR * offset)` (mod.rs:185-211). However, `ReceivedOutput::read` (mod.rs:122-134) reads `offset` via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`, and returns the composite without recomputing the script or checking the outpoint against any chain data. The fields are private, so the only way to produce an inconsistent `ReceivedOutput` in-process is through `read` — meaning the deserialization is precisely the unsanitized input path. The struct is then consumed by the spend path in `send.rs`, where `offset` re-keys the threshold key (`keys.offset(offset)` semantics) and `output.value`/`outpoint` drive sighash and fee math under `Prevouts::All`.

### Impact Explanation
An unprivileged party able to feed bytes to `ReceivedOutput::read` (e.g., a peer/service returning serialized outputs, or tampered persisted output data) can:

- Report outputs as received that are not spendable: an attacker-chosen `offset` paired with a `script_pubkey` that is not `key + offset·G` produces an output the wallet believes it owns but cannot ever validly sign for.
- Corrupt fee/change/dust computation: `TxOut::consensus_decode` accepts an arbitrary `value`, so the spend builder can be induced to compute fees and change against a fabricated amount.
- Cause signature-path divergence: the offset the signer applies will not correspond to the key the script requires, producing invalid transactions, or — combined with `scale`/negation edge cases — a spend under an unintended tweaked key.

This is a "funds reported received that are not spendable" / incorrect-verifier-formula class impact, meeting the Medium bar.

### Likelihood Explanation
Reachability requires serialized `ReceivedOutput`s to cross a trust boundary (coordinator-supplied data, network message, or disk state influenced by another party). The serialize/read round-trip API exists precisely for that transport, so the path is realistic; exploitation is limited to misreporting/corrupting spends rather than direct key recovery, so severity is Medium.

### Recommendation
In `ReceivedOutput::read`, or at the point of first use in `send.rs`, recompute `p2tr_script_buf(key + GENERATOR * offset)` and reject inputs where it doesn't equal `output.script_pubkey`. Alternatively, make `read` take the `Scanner` (or the base key) so the binding is enforced at deserialization, matching how `scan_transaction` constructs the value.

### Proof of Concept
```
// base key K, registered offset o, script S = p2tr(K + o*G)
let legit = scanner.scan_transaction(&tx); // ReceivedOutput { o, TxOut{script: S}, outpoint }
// Attacker crafts bytes:
//   offset' = arbitrary scalar (e.g., o+1)
//   TxOut'  = script_pubkey S (unchanged), value = inflated
//   OutPoint' = attacker's chosen outpoint
let forged = ReceivedOutput::read(&mut attacker_bytes).unwrap();
// forged.offset() = o+1, forged.output().script_pubkey = S
// Wallet spends to key K + (o+1)*G, but S requires K + o*G:
// -> output reported received, spend produces invalid signature / wrong fee math
```
No check in `ReceivedOutput::read` (mod.rs:122-134) rejects this mismatch.