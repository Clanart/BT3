### Title
`ReceivedOutput::read` accepts an unvalidated scalar offset, letting untrusted bytes report funds as spendable under the wrong key - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` couples a `TxOut`/`OutPoint` with the `Scalar` offset needed to derive the spending key, but `ReceivedOutput::read` deserializes the `offset` directly from the byte stream with no check that `key + offset * G` actually produces `output.script_pubkey`. When `ReceivedOutput`s are obtained through `Scanner::scan_transaction`/`scan_block`, the offset is looked up from `self.scripts` keyed by the matched `script_pubkey`, so the binding is guaranteed by construction. When a `ReceivedOutput` is instead reconstructed via `read`/`serialize` (e.g., received over an untrusted channel or from storage writable by an attacker), that invariant is silently dropped: any `offset` is accepted alongside any output.

### Finding Description
The bug class is "failure signaled by return value rather than enforced, producing incorrect accounting." Here the analogous shape is a check that exists on the honest path but is absent on the untrusted-bytes path:

- `Scanner::scan_transaction` only emits `ReceivedOutput`s whose `offset` was registered under exactly that `script_pubkey` in `self.scripts` (networks/bitcoin/src/wallet/mod.rs:199-214).
- `Scanner::register_offset` even increments offsets until the resulting tweaked key is even-Y and rejects colliding scripts, establishing the offset↔script binding (mod.rs:180-196).
- `ReceivedOutput::read`, however, does `Secp256k1::read_F(r)?` for the offset and consensus-decodes the `TxOut`/`OutPoint` with no consistency check between them (mod.rs:122-133). It cannot re-derive the script because `ReceivedOutput` does not store the wallet key.
- Downstream, `offset()` is exposed (mod.rs:101-103) and consumed by the spending path in `networks/bitcoin/src/wallet/send.rs`, where the offset is applied to the threshold key/signature (BIP-340 parity handling in `crypto.rs` via `x_only`/`needs_negation`). A wrong offset produces a signature under the wrong tweaked key, which the network will reject — the output is counted as received yet cannot be spent.

An unprivileged party who can feed bytes to `ReceivedOutput::read` (or overwrite a serialized `ReceivedOutput` before it is consumed) can claim any outpoint with an offset that does not derive its script.

### Impact Explanation
Funds are reported as received and spendable while not being spendable: the wallet credits the output, and the signing attempt built from the bogus `offset` yields an invalid BIP-340 signature, so the UTXO is effectively locked for that flow. Depending on the integrator, this can strand funds (accounting shows balance that cannot be moved) or wedge the signing pipeline with transactions that always fail to broadcast. This matches the accepted impact "funds reported received that are not spendable."

### Likelihood Explanation
Medium. Exploitation requires the attacker to control or corrupt the `ReceivedOutput` bytes presented to the wallet (the rules explicitly list `ReceivedOutput::read` as an untrusted-bytes entry point). No validator compromise, collusion, or leaked keys are needed — just attacker-influenced serialized data. The failure is silent: `read` returns `Ok`, and the inconsistency only surfaces as an invalid signature at spend time, making it harder to detect than a deserialization error.

### Recommendation
Store the wallet's (tweaked) base key in `ReceivedOutput` or pass it to `read`, and after deserialization verify the binding:

```rust
// in networks/bitcoin/src/wallet/mod.rs, ReceivedOutput::read
let expected = p2tr_script_buf(key + (ProjectivePoint::GENERATOR * offset))
    .ok_or_else(|| io::Error::other("odd tweaked key"))?;
if expected != output.script_pubkey {
    Err(io::Error::other("offset does not derive output script_pubkey"))?;
}
```

Alternatively, make `Scanner` the only constructor (privatize `read`, or have `read` return an unvalidated `RawReceivedOutput` that must be re-validated via `Scanner`) so every `ReceivedOutput` is guaranteed consistent by construction, mirroring what `scan_transaction` already enforces.

### Proof of Concept
```rust
// networks/bitcoin context; `key` is the wallet's tweaked-even base key
let real_offset = scanner.register_offset(offset).unwrap();
let spendable_script = p2tr_script_buf(key + ProjectivePoint::GENERATOR * real_offset).unwrap();

// Attacker serializes a ReceivedOutput pairing a real output to `spendable_script`
// with a DIFFERENT offset (e.g. real_offset + 1, or an unrelated scalar):
let forged = ReceivedOutput {
    offset: real_offset + Scalar::ONE,  // does not derive spendable_script
    output: real_txout_paying_to_spendable_script,
    outpoint: real_outpoint,
};
let bytes = forged.serialize();

// Wallet deserializes: read() returns Ok — no offset/script binding check
let received = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// received.value() is credited, received.offset() is used to sign.
// key + (received.offset() * G) != the tweaked key in spendable_script,
// so the BIP-340 signature produced in send.rs is invalid:
// the output is reported received yet is not spendable.
```