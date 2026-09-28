### Title
Unverified prevout binding: `SignableTransaction`/`multisig` trust attacker-supplied `ReceivedOutput` value and outpoint without checking they correspond to the real UTXO - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
Analogous to the Monero `get_spend_proof`/`check_spend_proof` bug (signature challenge bound to a caller-supplied `txid` rather than to the hash of the parsed transaction), `bitcoin-serai` builds and signs a Taproot spend from a `ReceivedOutput` whose `outpoint` (txid/vout) and `output` (TxOut value + script) are deserialized from untrusted bytes and never verified to belong to the same on-chain UTXO. `ReceivedOutput::read` performs no consistency check, and `SignableTransaction::new`/`multisig` only re-check the `script_pubkey` against the offset group key — never the `value`, and never that `previous_output` actually references a transaction paying that script.

### Finding Description
`ReceivedOutput::read` reads `offset`, `TxOut`, and `OutPoint` independently, with no binding between them. `SignableTransaction::new` consumes these fields: `input.outpoint` becomes `TxIn.previous_output`, and `input.output` becomes the entry in `prevouts`. `multisig` verifies only `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — the value and the outpoint's referent are unchecked. At signing time, `TransactionSignMachine::sign` computes `taproot_key_spend_signature_hash(i, &Prevouts::All(&self.tx.prevouts), TapSighashType::Default)`, so the FROST Schnorr signature commits to the *attacker-claimed* prevout value/script, not to data fetched and rebound from the chain.

Consequences depending on the forged pairing:

- **Outpoint of a real Serai UTXO + TxOut with a lower value than reality**: the signature is consensus-valid. The difference (`real_value - claimed_value`) is silently added to the miner fee, since fee = `sum(prevouts) - sum(outputs)` as committed in the sighash, and the real input is worth more than claimed. Funds are burned to fees.
- **Outpoint + TxOut claiming a higher value**: the produced transaction is invalid (BIP-341 commits to amounts), but a full threshold signature over an unintended message is still produced and the attempt is consumed.
- **Outpoint of an arbitrary (even foreign-key) UTXO + TxOut with Serai script_pubkey**: passes the `multisig` script check; the signed tx is invalid unless the referenced coin genuinely pays that script.

This is precisely the "respond to a request for identifier B with body A" substitution: here the identifier is the outpoint (txid/vout) and the substituted body is the TxOut, and nothing rebinds them before signing.

### Impact Explanation
An unprivileged party able to supply a `ReceivedOutput` (e.g., via `ReceivedOutput::read` of untrusted bytes in a data path feeding `SignableTransaction::new`) can cause the threshold group to produce a valid signature on a transaction that destroys value — the unaccounted difference between the real UTXO value and the claimed `TxOut.value` is paid as a miner fee, stealing funds from the Serai wallet without any signature forged. Alternatively it can make a signing attempt finalize an unbroadcastable (invalid) transaction, burning the signing session. Impact is direct loss of wallet funds / signing of an unintended message, consistent with Medium severity.

### Likelihood Explanation
Precondition is that attacker-influenced `ReceivedOutput` bytes reach a signer — the same class of untrusted input (`ReceivedOutput::read`) the scope rules designate reachable. It does not require key compromise, collusion, or validator misbehavior; only that the output's provenance (outpoint ↔ TxOut binding, which is normally guaranteed when `Scanner::scan_transaction` constructs it internally) is bypassed by deserialization. Any path where outputs are serialized/transmitted and re-read (processor DB, event payloads, coordinator-supplied plans) exposes it. The fee-burn variant requires a real Serai UTXO outpoint, which is public on-chain data.

### Recommendation
In `SignableTransaction::multisig` (or at `ReceivedOutput` ingestion), verify each prevout against the chain or a trusted source: fetch `outpoint.txid` and confirm `tx.output[vout] == input.output` (value and script_pubkey), analogous to Monero's `THROW_WALLET_EXCEPTION_IF(tx_hash != txid)` guard. Short of an RPC lookup inside this crate, document/enforce that `SignableTransaction::new` only accepts `ReceivedOutput`s produced by `Scanner` for blocks already confirmed, and consider a debug/test assertion comparing `prevouts[i]` to the output resolved at `tx.input[i].previous_output`.

### Proof of Concept
```rust
// networks/bitcoin context: k256 Scalar, frost ThresholdKeys<Secp256k1>, bitcoin types
// Attacker crafts a ReceivedOutput pairing a REAL Serai UTXO outpoint with a
// fabricated TxOut claiming a lower value.

let real_utxo_outpoint: OutPoint = /* outpoint of a 1.0 BTC output paying key K */;
let forged = {
  let mut buf = Vec::new();
  buf.extend(Scalar::ZERO.to_bytes());              // offset
  buf.extend(serialize(&TxOut {                     // claimed TxOut: real script, tiny value
    value: Amount::from_sat(10_000),                // actually 100_000_000 on-chain
    script_pubkey: p2tr_script_buf(K).unwrap(),
  }));
  buf.extend(serialize(&real_utxo_outpoint));
  ReceivedOutput::read::<&[u8]>(&mut buf.as_slice()).unwrap()  // no consistency check
};

let stx = SignableTransaction::new(
  vec![forged], &[(payment_script, 9_000)], None, None, fee_rate,
).unwrap();
// multisig() succeeds: prevouts[0].script_pubkey matches p2tr(key+0).
let machine = stx.multisig(&keys).unwrap();
// sign() produces shares for sighash committing prevouts[0].value = 10_000.
// The completed tx is consensus-valid, spends the 1.0 BTC UTXO,
// and pays ~0.9999 BTC - payment as miner fee.
```

Key code paths: `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`), field consumption in `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:175-185`, `253`), the script-only check in `multisig` (`send.rs:273-284`), and the sighash over `Prevouts::All` (`send.rs:373-390`).