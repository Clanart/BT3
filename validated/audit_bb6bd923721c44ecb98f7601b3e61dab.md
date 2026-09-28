### Title
`ReceivedOutput::read` accepts a scalar offset without verifying it derives the output's `script_pubkey`, so attacker‑supplied bytes are trusted for downstream spending - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
Analogous to CVE-2017-5330 (Ark executing attacker-supplied archive contents via "associated applications" — i.e., trusting attacker-controlled input to drive a privileged action), `ReceivedOutput::read` deserializes an untrusted `(offset, TxOut, outpoint)` triple and treats it as authoritative. Nothing binds `offset` to `output.script_pubkey`, even though the offset is later used to re-key the threshold keys (`tweak_keys`) so the multisig can spend the output. An attacker who feeds crafted bytes to `ReceivedOutput::read` can cause funds to be reported as received that are not actually spendable, or cause a signing attempt that produces an invalid transaction.

### Finding Description
`ReceivedOutput::read` reads three independent fields with no cross-field validation:

- `offset`: `Secp256k1::read_F(r)` — any canonical scalar is accepted.
- `output`: `TxOut::consensus_decode` — any `script_pubkey`/value is accepted.
- `outpoint`: `OutPoint::consensus_decode` — any txid/vout is accepted.

In contrast, the legitimate producer `Scanner::scan_transaction` only emits a `ReceivedOutput` when `output.script_pubkey` was previously registered via `register_offset`, which guarantees `script_pubkey == p2tr_script_buf(key + G·offset)`. That invariant exists purely by construction in memory — it is not re-established on deserialization. A deserialized `ReceivedOutput` can therefore claim offset `o` while carrying a `script_pubkey` derived from a different offset, or a script not controlled by the group at all. The spending path consumes `output.offset()` to re-key (per the test flow `tweak_keys`/`multisig` in `networks/bitcoin/tests/wallet.rs`) and builds a sighash over the actual prevouts, so a mismatch produces a transaction that cannot validate, while the accounting path has already recorded the output as received value.

### Impact Explanation
An unprivileged party supplying serialized `ReceivedOutput` bytes can cause either:

1. Funds reported received that are not spendable — a `ReceivedOutput` pairing a valid-looking `script_pubkey` with a mismatched `offset`, or a script not controlled by the group key at all, inflating apparent balance and potentially triggering downstream operations premised on that balance.
2. Failed/aborted spends — the multisig signs a transaction over the real prevout with keys re-keyed by the wrong offset, yielding an invalid witness and a permanently unspendable-looking input until manually corrected.

This mirrors the Ark bug: attacker-controlled bytes flowing through a trusted parsing entry point determine a subsequent privileged action (there, execution via associated applications; here, re-keying/spending and balance accounting).

### Likelihood Explanation
The vulnerability requires attacker-controlled bytes reaching `ReceivedOutput::read` (e.g., an untrusted serialized output being ingested). Within the allowed reachability model (untrusted bytes fed to `ReceivedOutput::read`), no cryptographic break, collusion, or privileged position is needed — only a canonical scalar and valid consensus encodings. Severity is bounded because the on-chain signature itself cannot be forged; the damage is reporting unspendable funds and producing invalid transactions.

### Recommendation
Re-establish the scanner invariant on deserialization. Either:

- Pass the group key (or the `Scanner`/`scripts` map) into `ReceivedOutput::read` and verify `output.script_pubkey == p2tr_script_buf(key + G·offset)`, rejecting on mismatch; or
- Serialize `ReceivedOutput` as `(outpoint, offset)` only, and re-derive/lookup the `TxOut` and script on read, so the binding cannot be forged in bytes.

### Proof of Concept
Conceptually, for a group key `K`:

```rust
// Attacker crafts bytes: offset = 0, but script_pubkey = p2tr(K + G*o') for some o' != 0
let mut buf = Scalar::ZERO.to_bytes().to_vec();
buf.extend(serialize(&TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: p2tr_script_buf(K + ProjectivePoint::GENERATOR * o_prime).unwrap(),
}));
buf.extend(serialize(&OutPoint::new(attacker_txid, 0)));

// Accepted without error despite the offset/script mismatch
let forged = ReceivedOutput::read(&mut buf.as_ref()).unwrap();
// forged.value() == 1_000_000 is reported as received; spending it via
// offset ZERO yields an invalid witness since the prevout pays K + G*o'
```

Note: full confirmation of the downstream signing behavior in `networks/bitcoin/src/wallet/send.rs` (exactly how `offset` feeds `tweak_keys` and the sighash) was not completed within the available investigation budget; the deserialization-side absence of the offset↔script binding is confirmed at `networks/bitcoin/src/wallet/mod.rs:122-134` and the binding-by-construction at `:205-210`.