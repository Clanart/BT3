### Title
`ReceivedOutput::read` accepts untrusted offset/output/outpoint tuples without verifying the offset actually spends the output, reporting unspendable funds as spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction`/`scan_block` is the only trusted producer of `ReceivedOutput`, and it guarantees the invariant that `output.script_pubkey == p2tr(key + offset * G)` because it only emits outputs whose `script_pubkey` was found in the registered `scripts` map (`wallet/mod.rs:199-214`). `ReceivedOutput::read` (`wallet/mod.rs:122-134`), however, reconstructs a `ReceivedOutput` from untrusted bytes by independently deserializing `offset`, `output`, and `outpoint` and never re-checks that invariant. The struct's own doc comment calls it "A spendable output" (`wallet/mod.rs:88-89`), so any downstream consumer treating a deserialized `ReceivedOutput` as spendable can be handed an output whose declared `offset` does not correspond to the key that locks the `TxOut`, or whose `outpoint` does not exist on-chain.

### Finding Description
The bug class from the external report is "a party can detach an asset from the obligation it secures — value is treated as free while a constraint still applies." In Serai's shape, `ReceivedOutput` is a claim of spendable collateral. The scanner enforces the binding between `offset` and `output.script_pubkey` at production time, but the deserialization path — explicitly reachable with attacker-controlled bytes via `ReceivedOutput::read` — drops that binding entirely. There is no validation that:

1. `p2tr_script_buf(key + G * offset)` equals `output.script_pubkey` (the `Scanner` has the `key` needed to do this; `read` doesn't even take the key).
2. The `outpoint` refers to a real, unspent output paying to that script.

The result is a `ReceivedOutput` that serializes, clones, and behaves identically to a scanner-produced one but fails at spend time (or worse, attributes spendability to an output locked by an arbitrary script, since `register_offset` itself warns that arbitrary offsets can embed attacker-chosen script paths, `wallet/mod.rs:177-179`).

### Impact Explanation
A consumer fed crafted bytes gets "funds reported received that are not spendable": the wallet/pipeline accounts the output as usable collateral, builds a spend transaction referencing a nonexistent or wrongly-keyed outpoint, and produces a transaction that will be rejected by the network — or, if the offset encodes a script path the attacker controls, directs signing effort toward an output spendable by a third-party script. This is a concrete integrity failure of the received-funds reporting path, not a formatting issue.

### Likelihood Explanation
Reachable by any unprivileged party who can supply bytes that reach `ReceivedOutput::read` (e.g., output data relayed between components, recovered state, or peer-supplied claims). Exploitation requires no key material and no validator status; it only requires the consumer to trust the deserialized claim rather than rescanning the chain.

### Recommendation
Have `ReceivedOutput` deserialization be key-aware: require the scanner's key (or the expected `script_pubkey`) at read time and verify `output.script_pubkey == p2tr_script_buf(key + G * offset)`, rejecting mismatches. Alternatively, restrict `read`/`write` to internal trusted persistence and never expose it on untrusted byte paths; at minimum, document that `read` does not establish spendability and consumers must re-verify against chain data.

### Proof of Concept
```rust
// Given Scanner for `key` with a registered offset `off` (script S).
// Honest path: scan_transaction produces ReceivedOutput { offset: off, output paying S, outpoint }.

// Attacker path: serialize a forged ReceivedOutput where `output.script_pubkey`
// is an arbitrary script_pubkey NOT derived from key + off*G:
let mut buf = Vec::new();
buf.extend(attacker_offset.to_bytes());                       // any scalar
buf.extend(bitcoin::consensus::encode::serialize(&TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: attacker_script,                          // not p2tr(key + off*G)
}));
buf.extend(bitcoin::consensus::encode::serialize(&fake_outpoint)); // may not exist

let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
// `forged` is indistinguishable from a scanner-produced output.
// Signing a spend with `offset(off)` produces a Schnorr signature under the
// wrong tweaked key -> transaction rejected; funds were reported spendable.
```

Relevant code: `ReceivedOutput::read` lacks any key/script binding check at `networks/bitcoin/src/wallet/mod.rs:122-134`, while the invariant it relies on is only enforced inside `Scanner::scan_transaction` at `networks/bitcoin/src/wallet/mod.rs:199-214`.

Caveat: this finding assumes a deployment path where `ReceivedOutput` bytes cross a trust boundary (relayed/recovered output claims). If the type is only ever round-tripped from trusted local storage, the reachable impact reduces to misuse rather than a remote vulnerability.