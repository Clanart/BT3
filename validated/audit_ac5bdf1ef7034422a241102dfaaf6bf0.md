### Title
`ReceivedOutput` does not bind the spent `OutPoint` to the committed `TxOut`, so the output verified/checked (`prevouts[i]`) may not be the output actually spent (`outpoint`) - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The JOJO bug class is "the target authorized/checked (`approveTarget`) may differ from the target actually invoked (`swapTarget`)". In `bitcoin-serai`, the analog lives in `SignableTransaction`/`TransactionMachine`: each input contributes two independently-supplied pieces — `outpoint` (the UTXO actually spent by `tx.input[i]`) and `output` (the `TxOut` committed into the Taproot sighash via `Prevouts::All` and the only thing `multisig` checks). `ReceivedOutput::read` accepts both as untrusted bytes with no consistency check, so an unprivileged party can submit a `ReceivedOutput` where the prevout committed to and checked is not the prevout actually consumed by the outpoint.

### Finding Description
`ReceivedOutput` stores `offset`, `output`, and `outpoint` as three independent fields; `ReceivedOutput::read` deserializes all three from attacker-controlled bytes without relating them (networks/bitcoin/src/wallet/mod.rs:122-133). `Scanner::scan_transaction` produces well-formed triples, but nothing requires a `ReceivedOutput` to come from the scanner.

In `SignableTransaction::new`, `input.outpoint` becomes `tx.input[i].previous_output` — the UTXO the transaction actually spends — while `input.output` becomes `prevouts[i]` (networks/bitcoin/src/wallet/send.rs:177-185, 253). `SignableTransaction::multisig` then checks only that `p2tr_script_buf(offset key) == self.prevouts[i].script_pubkey` (send.rs:275-281), i.e., it authenticates the *claimed* `TxOut`, not the on-chain output the `OutPoint` references. During `sign`, the Schnorr share is produced over `taproot_key_spend_signature_hash(i, &Prevouts::All(&self.tx.prevouts), Default)` (send.rs:373-390), committing to the attacker-chosen `output` value/script for every input.

So the object that is checked and bound into the signature (`prevouts[i]`, the "approved target") can differ from the object actually being spent (`previous_output`, the "swap target"). An attacker can reuse a real deposit's `TxOut` (same script_pubkey, so the `multisig` check passes) paired with a different `OutPoint`, or pair a fabricated amount with any outpoint. Additionally, because `Prevouts::All` commits to *all* prevouts, one mismatched `ReceivedOutput` corrupts the sighash of every input in the transaction.

### Impact Explanation
- If `prevouts[i]` does not equal the true UTXO at `previous_output`, BIP-341 key-path verification fails on-chain: the threshold produces a fully-signed transaction that is invalid and unbroadcastable. Since `Prevouts::All` binds every input, a single malicious `ReceivedOutput` invalidates all signatures in the batch, bricking the entire spend (denial of service of the payment batch, wasted FROST ceremony).
- Combined with a corrupted `output.value`, the fee accounting (`fee() = sum(prevouts) - sum(outputs)`, send.rs:137-141) and `NotEnoughFunds`/`Dust` checks in `new` are computed on claimed values, so the transaction can be constructed and signed under false balance assumptions.
- This is a "funds/authorization checked against the wrong object" flaw reachable purely from untrusted bytes fed to `ReceivedOutput::read` → `SignableTransaction::new` → `multisig` → `sign`/`complete`.

### Likelihood Explanation
Any component that accepts externally-sourced `ReceivedOutput`s (they are explicitly a deserializable type via `read`) can inject mismatched triples. The check in `multisig` gives false assurance: it verifies the script_pubkey is spendable by the offset key but never that the outpoint resolves to that TxOut, mirroring how JOJO's approval of `approveTarget` gave false assurance while `swapTarget` executed the swap.

### Recommendation
Bind the `output` to the `outpoint` before signing: e.g., re-fetch each `OutPoint`'s `TxOut` from a verified chain view (or require producers to prove `(outpoint → output)` consistency) and reject `ReceivedOutput`s whose declared `output` does not match the on-chain UTXO, rather than only checking the script_pubkey against the offset key in `multisig`.

### Proof of Concept
```rust
// Attacker-controlled bytes -> ReceivedOutput::read
// offset: valid deposit offset O (script check will pass)
// output: TxOut { value: victim_amount, script_pubkey: p2tr(key + O*G) } // copied from a real deposit
// outpoint: attacker-chosen OutPoint pointing to a DIFFERENT UTXO (or non-existent)

let ro = ReceivedOutput::read(&mut attacker_bytes)?;   // no consistency check
let stx = SignableTransaction::new(vec![ro], &payments, change, None, fee_rate)?; // checks pass on claimed value
let machine = stx.multisig(&keys).unwrap();            // script_pubkey matches offset key -> Some
// sign() commits to Prevouts::All([fabricated TxOut]) while input spends attacker's OutPoint
// -> signature invalid under BIP-341; entire batch (all inputs) is unbroadcastable
```