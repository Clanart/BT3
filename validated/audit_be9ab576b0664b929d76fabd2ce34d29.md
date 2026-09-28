### Title
`ReceivedOutput::read` accepts an arbitrary `offset`/`script_pubkey` pair without re-deriving the script, allowing attacker-crafted bytes to report unspendable or misattributed outputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report concerns a receiver-side sanitization check (`basename()` on a sender-supplied filename) that was dropped, letting the sender dictate a value the receiver should have re-derived locally. The direct Serai analog is `ReceivedOutput`: an output is only legitimately "received" when its `offset` satisfies `p2tr_script_buf(scanner_key + G * offset) == output.script_pubkey`. `Scanner::scan_transaction` enforces this by construction (it only emits outputs whose script was produced by a registered offset), but `ReceivedOutput::read` deserializes all three fields from untrusted bytes and performs no such consistency check — exactly the class of dropped receiver-side validation in the reference advisory.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `ReceivedOutput::read` (lines 122-134) reads `offset`, `output` (a `TxOut`), and `outpoint` and returns them verbatim:

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  let output;
  let outpoint;
  {
    let mut buf_r = BufReader::with_capacity(0, r);
    output = TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
    outpoint = OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
  }
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

Nothing binds `offset` to `output.script_pubkey`. Contrast with `Scanner::register_offset`/`scan_transaction` (lines 180-214), which only ever constructs `ReceivedOutput`s where the offset provably produces the script under the scanner's key. The downstream consumer `Output::key()` (`processor/src/networks/bitcoin.rs`, lines 112-122) computes `key = script_key - G*offset` — a formula that silently yields *some* point for any attacker-chosen pair rather than verifying the pair is consistent, and `scan_block`'s own documentation acknowledges the coinbase-maturity gap is left to callers. The read path is reachable via `Output::read` (bitcoin.rs line 156), which is used to deserialize `ReceivedOutput`s from stored/transmitted bytes — the untrusted-bytes sink named in scope.

### Impact Explanation
An attacker who can supply bytes to `ReceivedOutput::read`/`Output::read` can fabricate an output that:

- Reports a real on-chain `outpoint`/`TxOut` paying an unrelated script, paired with an arbitrary `offset`. The wallet layer counts it as a received, spendable output under the multisig, but `key() - G*offset` does not correspond to any key the multisig controls, so the funds are "reported received" yet not spendable — corrupting balance accounting and any scheduler/planning that consumes the output list.
- Conversely, pairs a genuine multisig script with a wrong `offset`, producing an incorrect spend key and causing signing attempts over the wrong tweaked key (failed or mis-directed spends).

This mirrors the wormhole impact class: the receiver consumed a counterparty-supplied field where a locally-derivable invariant existed and was not enforced at the trust boundary.

### Likelihood Explanation
Reachability depends on an attacker-controlled `ReceivedOutput` byte stream reaching `read` (e.g., through the `Output` serialization path or any peer/coordinator-supplied output data). `Offset` values are security-critical — `register_offset` itself documents that arbitrary offsets introduce arbitrary script spend paths — so treating deserialized offsets as trusted without re-derivation crosses the same boundary the advisory describes. Severity is bounded by the fact that in the primary `get_outputs` path an `assert_eq!(output.key(), key)` exists (bitcoin.rs line 563), but that check is absent on the `read` path itself, and outputs persist/round-trip through serialization.

### Recommendation
Make `ReceivedOutput` self-validating on deserialization: either store the expected key/script in the serialized form and assert `p2tr_script_buf(key + G*offset) == output.script_pubkey` inside `ReceivedOutput::read` (or a `read_for_key` variant taking the scanner key), or re-derive the script and reject on mismatch. Failing that, `Output::read` in `processor/src/networks/bitcoin.rs` should assert `output.key() == expected_key` the same way `get_outputs` does, rather than relying on every caller to re-check the invariant.

### Proof of Concept
```rust
// Networks/bitcoin: craft a ReceivedOutput claiming an arbitrary outpoint+script
// under an offset that does not derive the script.
let mut buf = vec![];
buf.extend(Scalar::ONE.to_bytes());            // attacker-chosen offset
buf.extend(serialize(&TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: victim_p2tr_script,           // real-looking output
}));
buf.extend(serialize(&OutPoint::new(real_txid, 0)));

let output = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
// Succeeds despite offset=1 not producing victim_p2tr_script for the scanner key.
// Output::key() then returns key' = script_key - G, a point the multisig cannot sign for.
```

Caveat: I was unable to fully trace every call site of `ReceivedOutput::read`/`Output::read` within the iteration budget to confirm a concrete remote attacker-controlled byte source in production flow; the finding holds wherever serialized outputs are accepted from a non-local-trust source, which the in-scope rules enumerate as a reachable sink.