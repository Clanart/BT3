### Title
`SignableTransaction`/`ReceivedOutput` accept unverified prevout data (`outpoint`, `TxOut` value, `offset`) that is never checked against the actual UTXO set, causing the FROST multisig to sign a BIP-341 sighash committing to fabricated prevouts - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs), [File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
CVE-2019-19859's class is "accept unlimited attacker-supplied data even if it does not match anything in the database." The Serai analog is `SignableTransaction::new`, which ingests `Vec<ReceivedOutput>` — each of which can be materialized from raw bytes by `ReceivedOutput::read` — and uses `input.outpoint` and `input.output` (value + script_pubkey) verbatim as the transaction's inputs and as the `Prevouts::All` commitment for the Taproot sighash. No code path verifies that the claimed `outpoint` exists on-chain, that the claimed `value`/`script_pubkey` match the real UTXO, or that the claimed `offset` is the offset the `Scanner` originally derived for that output. The only consistency check is `multisig()` verifying `p2tr_script_buf(key + G*offset) == prevout.script_pubkey`, which binds offset to script but leaves `outpoint` and `value` completely unbound.

### Finding Description
`ReceivedOutput::read` deserializes three attacker-controlled fields with no cross-validation: an `offset` scalar, a `TxOut`, and an `OutPoint` (`networks/bitcoin/src/wallet/mod.rs:122-134`). `SignableTransaction::new` then (a) sums `input.output.value` into `input_sat` to decide solvency and the change amount (`send.rs:175`, `send.rs:224-235`), (b) copies `input.outpoint` into the signed `tx.input` (`send.rs:179-185`), and (c) stores `input.output` into `prevouts` (`send.rs:253`). During signing, `TransactionSignMachine::sign` computes `cache.taproot_key_spend_signature_hash(i, &Prevouts::All(&self.tx.prevouts), TapSighashType::Default)` (`send.rs:373-390`) — i.e., the FROST group signs a digest that commits to the *claimed* prevout set, not the real one. There is no lookup of the outpoint against a node or any comparison of claimed vs. actual UTXO data anywhere in `bitcoin-serai`.

### Impact Explanation
Two concrete consequences:

1. **Signing of an unintended message.** An unprivileged party feeding bytes to `ReceivedOutput::read` can make the threshold group produce a valid BIP-340 Schnorr signature over a BIP-341 sighash committing to entirely fabricated prevout data (fake amounts, fake outpoints). The signer intended to sign "a spend of our real UTXOs"; it instead signs an attacker-chosen digest. Because the attacker controls the tx body and all prevout fields, this is effectively a constrained signing oracle under the tweaked group key.
2. **Unspendable transactions / ledger inconsistency.** If the fabricated `value` differs from the real UTXO's amount, consensus nodes compute a different sighash, so the signed transaction is invalid and unbroadcastable — yet `SignableTransaction::new` happily computed `input_sat`, payments, change, and `needed_fee` against the phantom value. Downstream, an inflated `value` makes a bogus input appear to fund payments; a deflated `value` mis-sizes change.

### Likelihood Explanation
Reachable wherever `ReceivedOutput` objects are reconstructed from untrusted bytes (explicitly in scope for `ReceivedOutput::read`) or supplied by any party who did not obtain them from `Scanner::scan_*`. The scanner itself is safe — it only emits outputs seen on-chain — but nothing enforces that provenance: the type is a plain data container and `SignableTransaction::new`/`multisig` perform no on-chain validation. Exploitation requires no key material, no collusion, and no validator privilege — only the ability to feed a crafted `ReceivedOutput` into the spend path. Impact is bounded by Bitcoin consensus (a mismatched prevout produces an invalid, not stealthy, transaction), which is why this is Medium rather than High.

### Recommendation
- In `SignableTransaction::new` (or a verified constructor for `ReceivedOutput`), fetch each claimed `outpoint` from a Bitcoin node and assert the real `TxOut` equals `input.output` before admitting it as an input; reject unknown or mismatched outpoints.
- Verify at `ReceivedOutput::read`/construction time that `p2tr_script_buf(group_key + G*offset) == output.script_pubkey` rather than deferring a partial check to `multisig()` (which also silently returns `None` instead of an error).
- Treat `Scanner`-produced outputs and deserialized `ReceivedOutput`s as different trust domains (e.g., a private constructor or a validity proof field) so byte-deserialized outputs cannot masquerade as scanned ones.

### Proof of Concept
```rust
// Attacker-controlled bytes -> ReceivedOutput with a fabricated prevout.
// offset/script consistency passes multisig(); outpoint and value are never checked.
let mut bytes = Vec::new();
bytes.extend(real_offset.to_bytes());              // offset matching a registered script
bytes.extend(serialize(&TxOut {
    value: Amount::from_sat(10_000_000),           // fabricated: real UTXO is 100_000 sats
    script_pubkey: p2tr_script_buf(key + (GENERATOR * real_offset)).unwrap(),
}));
bytes.extend(serialize(&OutPoint {
    txid: arbitrary_txid,                          // may not exist / may not pay to us
    vout: 0,
}));
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// SignableTransaction::new accepts it: input_sat counts 10_000_000 sats of phantom funds,
// payments/change/fee are computed against it, and Prevouts::All commits to the fake TxOut.
let tx = SignableTransaction::new(vec![forged], &payments, Some(change), None, fee).unwrap();
// multisig() returns Some because p2tr_script_buf(key+offset) == claimed script_pubkey.
// The FROST group then signs a sighash committing to prevouts that do not match the chain:
// the signature is valid for that fabricated digest but the transaction is unspendable,
// while the signing session was induced to authorize a message it never intended.
```