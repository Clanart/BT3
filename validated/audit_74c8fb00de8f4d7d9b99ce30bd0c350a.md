### Title
`ReceivedOutput::read` trusts an attacker-supplied offset/script pair, so forged bytes report received outputs that are not spendable - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
The `jku` vulnerability class is "a protocol message carries a pointer to key material, and the consumer trusts it by default without verifying it actually identifies the intended key." In Serai's Bitcoin wallet, `ReceivedOutput` is exactly such a pairing: an `offset` scalar (the pointer telling the wallet which derived key can spend) bundled with a `TxOut`/`OutPoint` (the claimed funds). `Scanner::scan_transaction` establishes the `script_pubkey → offset` binding cryptographically via `p2tr_script_buf(key + G·offset)`, but `ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` from raw bytes with no check that the offset actually derives the output's `script_pubkey`. Any untrusted byte stream fed to `read` can therefore claim arbitrary outputs are spendable by the wallet when they are not.

### Finding Description
`ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) performs:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)?;
outpoint = OutPoint::consensus_decode(&mut buf_r)?;
Ok(ReceivedOutput { offset, output, outpoint })
```

It never verifies that `p2tr_script_buf(scanner.key + G·offset) == output.script_pubkey`. That binding is the whole security property of `Scanner` (`mod.rs:185-191`, `205-211`): on the scan path, an output is only accepted because its `script_pubkey` was precomputed from a registered offset. On the deserialization path, the binding is simply asserted by the byte stream. Downstream, `send.rs` consumes `output.offset()` to call `keys.offset(...)` and produce a Schnorr signature for the input. If the offset does not correspond to the output's script key, the signature is valid for the wrong public key — the input cannot be spent, yet the wallet's bookkeeping treated it as a received, owned output.

### Impact Explanation
An unprivileged party who can supply bytes to `ReceivedOutput::read` (e.g., relayed/bridged serialized outputs between components) can report phantom or misattributed deposits: an output paying to an unrelated script, paired with any offset, is accepted as spendable wallet funds. This yields "funds reported received that are not spendable" — balance over-reporting and construction of transactions that can never confirm, the analog of the advisory's attacker-controlled key-source claim being trusted.

### Likelihood Explanation
Exploitation requires the attacker to reach the serialized `ReceivedOutput` channel rather than the chain-scanning path. The struct exposes `serialize`/`read` specifically for transport across trust boundaries; nothing in the type, comments, or call sites documents that these bytes must only come from a trusted local `Scanner`. Where they do cross a boundary, no signature, MAC, or consistency check protects them.

### Recommendation
Make `ReceivedOutput` self-validating: either (a) include the spendable script check at construction — store the scanner key or expected `script_pubkey` inside `ReceivedOutput` and have `read` recompute `p2tr_script_buf(key + G·offset)` and reject mismatches — or (b) add a `verify(key: ProjectivePoint) -> bool` method that asserts the offset derives `output.script_pubkey`, and require callers to invoke it after deserialization. At minimum, document that `read` input must be authenticated.

### Proof of Concept
```rust
// Attacker chooses any offset and any TxOut paying to their own script
let mut bytes = Vec::new();
bytes.extend(Scalar::ONE.to_bytes());                 // claimed offset
bytes.extend(serialize(&TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: attacker_p2tr_script,               // not derived from offset 1
}));
bytes.extend(serialize(&OutPoint::new(txid, 0)));

let received = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// read() succeeds; wallet records `received` as spendable via key.offset(ONE),
// but the output's script_pubkey was never derived from that key.
// Any spend attempt produces a signature for the wrong pubkey -> unspendable.
```