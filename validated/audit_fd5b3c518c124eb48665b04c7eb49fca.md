### Title
Untrusted `ReceivedOutput` bytes are never validated against the chain, letting an attacker fabricate inputs whose claimed value/outpoint is signed and reported as spendable — (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes three completely independent fields — a scalar `offset`, a `TxOut` (script + value), and an `OutPoint` — with no consistency check between them (`networks/bitcoin/src/wallet/mod.rs:122-134`). `SignableTransaction::new` and `SignableTransaction::multisig` then trust this data end-to-end: the only validation performed is that `p2tr_script_buf(group_key + offset·G) == prevout.script_pubkey` (`send.rs:273-282`). Nothing verifies that the claimed `outpoint` actually exists on-chain, resolves to the claimed `TxOut`, or carries the claimed value — directly analogous to the Flowise SSRF, where an attacker-supplied destination/input was consumed without the required validation step.

### Finding Description
The SSRF class here is "attacker-supplied object reaching a sensitive sink without validation":

- `ReceivedOutput::read` reads `offset` via `Secp256k1::read_F`, then consensus-decodes a `TxOut` and an `OutPoint`. All three are arbitrary and unbound to each other (`mod.rs:122-133`).
- `SignableTransaction::new` sums `input.output.value` for the funds check (`send.rs:175`), computes fees and change against those claimed values, and stores `input.output` into `prevouts` (`send.rs:253`).
- `TransactionSignMachine::sign` builds `Prevouts::All(&self.tx.prevouts)` and produces a real FROST signature over `taproot_key_spend_signature_hash`, committing to the fabricated `TxOut`s (`send.rs:373-390`).
- `multisig()` only checks the script/offset correspondence (`send.rs:277`), never `outpoint → TxOut`.

An unprivileged party feeding crafted bytes to `ReceivedOutput::read` can therefore mint a `ReceivedOutput` claiming an arbitrary value at any outpoint whose script pays to a registered key/offset (e.g., a dust output the attacker itself sent), inflated to any claimed value. `Scanner`-independent consumers never re-derive the output from the block.

### Impact Explanation
Two concrete impacts within the accepted criteria:

1. **Signing of an unintended message / funds reported received that are not spendable.** A fabricated `ReceivedOutput` inflating a real UTXO's value passes `NotEnoughFunds`, produces a fully valid FROST signature over `Prevouts::All`, and yields a transaction that is consensus-invalid (prevout amount/commitment mismatch or nonexistent outpoint). The downstream system records the spend as signed/broadcast and reports the claimed funds as received, while they are unspendable — payments fail silently until manual intervention, and the fabricated "deposit" inflates accounting.
2. Because `input_sat` is computed purely from attacker bytes, the fee/change path can be driven to arbitrary branches, burning multisig nonce preprocesses on transactions that can never confirm.

### Likelihood Explanation
`ReceivedOutput::read` is explicitly a network-facing deserialization entry point, and any code path that persists or relays scanned outputs (e.g., `Output::read` calling `ReceivedOutput::read`) accepts these bytes. Crafting them requires only knowledge of a registered script (derivable from any real deposit) — an attacker who sent one legitimate dust deposit can reuse its script and forge the value. No threshold collusion or validator compromise is needed. Severity: Medium (no key extraction, but fabricated financial state and signed-yet-unspendable transactions).

### Recommendation
Bind and verify: (a) in `ReceivedOutput::read` or a new `verify` step, resolve `outpoint` against the chain/RPC and check the returned `TxOut` equals `self.output` (script and value); (b) in `SignableTransaction::multisig`, additionally reject duplicate outpoints; (c) store only the outpoint + offset and re-derive the `TxOut` at use time rather than trusting serialized values.

### Proof of Concept
```rust
// Attacker knows a script paying to key + offset*G (e.g., their own dust deposit).
let script = p2tr_script_buf(key + (GENERATOR * offset)).unwrap();
let mut bytes = Vec::new();
offset.to_bytes();                  // write real offset
// consensus-encode TxOut { value: 21_000_000_0000_0000, script_pubkey: script }
// consensus-encode OutPoint pointing at the attacker's real dust UTXO (or a fake one)
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// forged.value() == 21M BTC; SignableTransaction::new(vec![forged], ...) passes
// NotEnoughFunds, multisig() passes the script check, and sign() produces a
// real FROST signature over Prevouts::All containing the fabricated TxOut.
```