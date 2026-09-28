### Title
`ReceivedOutput::read` accepts attacker-supplied outputs whose `script_pubkey` is not bound to the claimed offset/key, letting malformed data pass the implicit "is this ours" check - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput` couples three attacker-controlled fields — a scalar `offset`, a `TxOut`, and an `OutPoint` — but `ReceivedOutput::read` deserializes all three with no consistency check between them. Analogous to the malformed-URL that passes a same-origin check, a malformed serialized `ReceivedOutput` passes deserialization "validation" while describing an output the group key cannot actually spend, or an offset that does not correspond to the output's `script_pubkey`.

### Finding Description
`Scanner::scan_transaction` constructs `ReceivedOutput` only after confirming `output.script_pubkey` is a registered script, i.e. `script_pubkey == p2tr_script_buf(key + G*offset)` (networks/bitcoin/src/wallet/mod.rs:199-214). That binding is the "origin check" of this type: the `offset` is only meaningful if it derives the key embedded in the output's script.

`ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134) performs no such check:

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  let output = TxOut::consensus_decode(&mut buf_r)...;
  let outpoint = OutPoint::consensus_decode(&mut buf_r)...;
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

Every field is taken verbatim from the byte stream. An unprivileged party feeding bytes to `ReceivedOutput::read` can therefore produce a struct claiming:

1. A legitimate `outpoint`/`output` paying to the group's script, but a bogus `offset` — when spent, `tweak_keys`/offset application yields a key that does not correspond to the script, so the signed input is unspendable.
2. A `TxOut` with an arbitrary `script_pubkey` (paying to the attacker) paired with a valid offset — the wallet layer reports value it cannot spend.
3. An `outpoint` referencing a nonexistent or already-spent UTXO with an inflated `output.value` — fee/change and funding math in `wallet/send.rs` consumes `output.value` (`ReceivedOutput::value()`) to decide funding, change, and dust, so a forged value skews transaction construction.

### Impact Explanation
A downstream consumer that reads `ReceivedOutput`s from untrusted bytes (e.g., an external scanner/relayer feeding outputs into the signing pipeline) will treat malformed outputs as received, spendable funds. Concrete impacts:

- **Funds reported received that are not spendable**: outputs counted at `output.value` whose real script is not controlled by `key + G*offset`, corrupting balance accounting.
- **Signing of an unintended spend**: the offset is applied per-input to re-key the threshold keys; a mismatched offset produces a signature for a different key than the input requires, or — if the `outpoint`/`TxOut` pair was forged to reference a real group UTXO with a different `value` — corrupts the sighash-computed amounts/change the group signs over (`Prevouts::All` commits to each input's value), causing the constructed transaction to commit fees/change incorrectly.

### Likelihood Explanation
The struct is explicitly documented in scope as an untrusted-bytes entry point (`ReceivedOutput::read`). Exploitation requires an attacker to control or influence the serialized `ReceivedOutput` stream consumed by a wallet/processor instance — e.g., a compromised or malicious output-reporting channel, or any peer-supplied message embedding this serialization. It does not require control of the threshold group or validator collusion; it requires only that the integration reads these bytes from an untrusted source, which is the intended use of a `read` API.

### Recommendation
Add the binding check at read/deserialization or first-use time: given the scanner's base `key`, verify `output.script_pubkey == p2tr_script_buf(key + GENERATOR * offset)` before accepting the `ReceivedOutput`. Alternatively, store/parse the `ReceivedOutput` as `(outpoint, offset)` only and re-derive the `TxOut` from chain data via the RPC, discarding attacker-supplied `script_pubkey`/`value`. A regression test should feed `ReceivedOutput::read` a serialized record whose `TxOut` script does not match `key + G*offset` and assert rejection.

### Proof of Concept
```rust
// Attacker-controlled bytes: valid scalar, attacker-owned script, forged outpoint
let mut buf = vec![];
buf.extend(Scalar::ONE.to_bytes()); // offset = 1, key = K + G
let evil = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: ScriptBuf::new_p2tr_tweaked(attacker_tweaked_key), // not our script
};
buf.extend(serialize(&evil));
buf.extend(serialize(&OutPoint::new(real_group_utxo_txid, 0)));

let ro = ReceivedOutput::read(&mut &buf[..]).unwrap(); // accepted: no binding check
// ro.value() == 1_000_000 counted as received; spend via offset=1 produces a
// signature under K+G which cannot unlock `evil.script_pubkey`, or if the
// outpoint is real, the inflated `value` corrupts sighash Prevouts::All
// commitments and fee/change math.
```