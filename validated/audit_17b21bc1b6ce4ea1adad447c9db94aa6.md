### Title
`ReceivedOutput::read` accepts an arbitrary (offset, script_pubkey) pairing without verifying consistency, letting untrusted bytes register unspendable outputs as received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The path-traversal class — unsanitized input locating an internal resource — maps onto `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs:122`. A `ReceivedOutput` is a claim that `outpoint`'s `script_pubkey` is spendable as `group_key + offset·G`, but the deserializer reads all three fields straight off the wire and never checks that the claimed `offset` actually derives the `script_pubkey` it is paired with. Any unprivileged party able to feed bytes to `ReceivedOutput::read` (explicitly in-scope untrusted input) can claim an output against an offset that does not correspond to it, producing a `ReceivedOutput` that reports value as received yet whose spendable key is unknown to the threshold group.

### Finding Description
`ReceivedOutput` bundles `offset`, `output` (a `TxOut`), and `outpoint`. The only honest constructor, `Scanner::scan_transaction` (`wallet/mod.rs:205-211`), only ever inserts an output when `self.scripts.get(&output.script_pubkey)` returns the offset previously registered for that exact script — i.e., `script_pubkey == p2tr_script_buf(key + offset·G)`. That invariant is what makes `offset` meaningful.

`ReceivedOutput::read` (`wallet/mod.rs:122-134`) performs no such check:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)...;
outpoint = OutPoint::consensus_decode(&mut buf_r)...;
Ok(ReceivedOutput { offset, output, outpoint })
```

It deserializes any canonical scalar as `offset` plus any `TxOut`/`OutPoint`, with no binding between them and no reference to the group key. Downstream, `OutputTrait::key()` (`processor/src/networks/bitcoin.rs:112-122`) recovers the spendable key by computing `script_key − offset·G` — an operation that returns *some* key for any pair, correct only when the invariant held. The spend path then requires threshold signing under share `+ offset`; if the offset does not actually map the script to the group key, the resulting signature is invalid and the output can never be spent.

### Impact Explanation
The analogous harm to reading arbitrary files is "funds reported received that are not spendable" — one of the accepted impact criteria. A crafted `ReceivedOutput` blob (e.g., a real high-value `TxOut`/`OutPoint` from the chain combined with a wrong scalar offset, or a script_pubkey matching one registered offset paired with a different offset value) passes deserialization cleanly, reports `value()` normally, and is only discovered to be unspendable when the multisig attempts to sign for it, at which point `verify_share` fails and the funds are effectively stranded/DoS'd for that input. This is directly reachable wherever `ReceivedOutput`/`Output` blobs cross a trust boundary (message queue, coordinator submission, persisted processor DB via `Output::read` at `processor/src/networks/bitcoin.rs:156`).

### Likelihood Explanation
Reachability requires only that untrusted bytes reach `ReceivedOutput::read`, which the prompt treats as in-scope. The decode itself is trivially satisfiable (one canonical scalar + valid consensus encodings); no cryptographic break is needed. Impact is bounded to integrity/availability of the affected output accounting rather than key recovery, so this rates Medium.

### Recommendation
Make `ReceivedOutput` deserialization verify the derivation invariant. Options:

- Change `ReceivedOutput::read` to take the group key (or the `Scanner`), and reject unless `output.script_pubkey == p2tr_script_buf(key + offset·G)` returns `Some`.
- Alternatively, store only `(outpoint, script_pubkey)` plus an authenticated offset tag, and re-derive/verify the offset through the `scripts` map on load rather than trusting it.
- At minimum, validate in `Output::read` / `Output::key` that `p2tr_script_buf(key() + offset·G)` reproduces `output.script_pubkey` before the output is credited.

### Proof of Concept
```rust
// Let `key` be the group key and `real` an honestly scanned output:
//   real.offset() = ZERO, real.output().script_pubkey = p2tr(key)

// Attacker crafts a blob claiming the same TxOut under a bogus offset:
let mut blob = vec![];
blob.extend(Scalar::from(1u64).to_bytes());            // offset = 1 (wrong)
blob.extend(&serialize(real.output()));                 // real p2tr(key) TxOut
blob.extend(&serialize(real.outpoint()));               // real outpoint

let forged = ReceivedOutput::read(&mut blob.as_slice()).unwrap(); // succeeds
assert_eq!(forged.value(), real.value());               // reported as received

// Spendable key recovered downstream:
//   script_key - 1*G  ==  key - 1*G  !=  group_key
// Signing with (share + 1) produces an invalid signature; output is unspendable.
```

Uncertainty: I verified the deserialization path, the scanner's script-keyed offset map, and the downstream key-recovery arithmetic. I could not fully confirm every call site where `ReceivedOutput::read`/`Output::read` consumes attacker-controlled bytes (e.g., exact message-queue framing), so the trust-boundary crossing is assumed per the prompt's rule that untrusted bytes to `ReceivedOutput::read` are in scope.