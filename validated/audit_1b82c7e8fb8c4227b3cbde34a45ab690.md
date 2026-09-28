### Title
Attacker-supplied `ReceivedOutput` acts as an unverified pointer into arbitrary wallet UTXOs, letting untrusted bytes select any owned outpoint for signing - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`ReceivedOutput::read` deserializes an offset scalar, a `TxOut`, and an `OutPoint` from raw bytes (`networks/bitcoin/src/wallet/mod.rs:122`). `SignableTransaction::new` then uses these attacker-controlled fields directly as `prevouts`/`offsets`/inputs (`send.rs:175-185`), and `multisig()` only checks that `p2tr_script_buf(key + offset*G) == prevouts[i].script_pubkey` (`send.rs:273-281`). Nothing verifies the outpoint actually resolves to an output the wallet intended to spend — analogous to CWE-59 link following: the `OutPoint`+`offset` pair is an attacker-supplied "symlink" that is followed to an unintended target. Since the sighash uses `Prevouts::All` (which commits to the supplied `TxOut`s) rather than resolving the real on-chain output, the attacker can direct the threshold signature at any UTXO whose script/value are publicly known, choosing the payments arbitrarily.

### Finding Description
`ReceivedOutput::read` performs no validation that the decoded `(offset, output, outpoint)` triple is self-consistent or wallet-authorized — the fields are accepted verbatim (`mod.rs:122-134`). In `SignableTransaction::multisig`, the only authenticity check is that the offset-derived key's P2TR script equals the *attacker-supplied* `prevouts[i].script_pubkey` (`send.rs:277`). This check proves consistency between two attacker-chosen fields; it never confirms the outpoint refers to a real output under wallet control, nor that the claimed `value` matches the chain. The signing path (`TransactionSignMachine::sign`, `send.rs:355-397`) then computes `taproot_key_spend_signature_hash` over `Prevouts::All(&self.tx.prevouts)` — committing to the attacker's claimed prevout data — and produces a valid BIP340 signature for `key + offset*G`. The registered offsets are derivable from public constants (`hash_to_F(KEY_DST, b"branch"/"change"/"forward")`, and `0` for external outputs), so an attacker can satisfy the script check for any wallet-controlled script type.

### Impact Explanation
For a real on-chain UTXO paying to a wallet P2TR script (offset `0`, or any known registered offset), the attacker supplies a `ReceivedOutput` naming that outpoint, the correct public `TxOut` (script and value are on-chain and public), and payments directed to attacker addresses. The consistency check in `multisig()` passes, and the resulting FROST signature produces a valid consensus-legal transaction spending that UTXO to the attacker. This is concrete signing of an unintended message: a fully valid spend of wallet funds selected entirely by attacker bytes, classified Medium-High depending on how `ReceivedOutput::read` is exposed by the integrator.

### Likelihood Explanation
The trigger requires untrusted bytes to reach `ReceivedOutput::read` and then `SignableTransaction::new`. The scanner path is safe (`scan_transaction` derives offsets internally), but any flow where serialized outputs are relayed between participants (e.g., forwarded/aggregation inputs constructed from peer-supplied encodings) exposes this. Value and script constraints are trivially satisfiable since both are public on-chain data.

### Recommendation
Bind `ReceivedOutput` to scanner provenance: either make it unforgeable (only constructible via `Scanner::scan_*`, e.g., by authenticating serialized forms with a MAC or a scanner-derived token), or have `multisig()`/`SignableTransaction::new` re-verify each input against `Scanner`-registered data — at minimum confirm `outpoint` corresponds to a previously scanned output and that `output` equals the on-chain `TxOut` (value and script), not merely that the claimed script is self-consistent with the claimed offset.

### Proof of Concept
```rust
// Attacker learns a real wallet UTXO: (outpoint, script_pubkey, value) from chain data.
// offset = Scalar::ZERO for the external (untweaked-offset) deposit script;
// or Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"branch") etc. for others.
let mut bytes = offset.to_bytes().to_vec();
bytes.extend(serialize(&real_txout));   // correct public script_pubkey + value
bytes.extend(serialize(&real_outpoint));
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// multisig() check passes: p2tr_script_buf(key + offset*G) == real_txout.script_pubkey
let tx = SignableTransaction::new(
  vec![forged],
  &[(attacker_script, value - fee)], // payment to attacker
  None, None, fee_per_vbyte,
).unwrap();
let machine = tx.multisig(&keys).unwrap(); // returns Some; FROST signs a valid spend
```