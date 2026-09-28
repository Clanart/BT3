### Title
`ReceivedOutput` trusts its embedded `TxOut.value`, letting an understated prevout inflate the miner fee / an overstated prevout produce an invalid transaction — (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
Analogous to the Allo fee-on-transfer report — where the accounting value (`poolAmount`) diverges from the actual token balance and poisons a later distribution — `SignableTransaction` in bitcoin-serai performs all of its balance arithmetic on the *claimed* `value` inside each `ReceivedOutput`, which is deserializable from untrusted bytes via `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122`). Nothing binds that claimed value (or the `outpoint`) to the real UTXO set: `SignableTransaction::multisig` only verifies that the prevout's `script_pubkey` matches the offset group key (`send.rs:277`), and the signed sighash commits to the claimed prevouts via `Prevouts::All` (`send.rs:375`).

### Finding Description
`ReceivedOutput::read` accepts an attacker-influenced `TxOut` — including its `value` and `script_pubkey` — and an arbitrary `OutPoint` (`mod.rs:128-131`). `SignableTransaction::new` then:

- sums `input.output.value` into `input_sat` (`send.rs:175`),
- computes the change output as `input_sat - payment_sat - fee_with_change` (`send.rs:228-234`),
- stores the *claimed* `TxOut`s as `prevouts` (`send.rs:253`).

During signing, `taproot_key_spend_signature_hash` is computed over `Prevouts::All(&self.tx.prevouts)` (`send.rs:375,386`), i.e. the BIP-341 sighash commits to the attacker-supplied amounts. Two divergence cases:

1. **Understated value (claimed < actual):** `input_sat` is smaller than the real spendable balance. The change output is computed from the understated sum, so the difference `actual - claimed` is not sent to change — it becomes part of the actual fee (`sum(inputs) - sum(outputs)`, `send.rs:139`). The transaction is *valid* on-chain (Taproot sighash commits to the claimed amount only for signing; the consensus rule only requires the real inputs cover the real outputs), but the excess is paid to miners permanently. This is the direct analog of Allo's "received less than accounted": here the wallet *overpays* by exactly the discrepancy, and it is unrecoverable.
2. **Overstated value (claimed > actual) or fake outpoint:** `NotEnoughFunds` is bypassed, an inflated change output is created, and the resulting sighash commits to amounts that don't match the real UTXO — every produced signature is invalid, the transaction can never confirm, and the signing session (FROST preprocesses + shares across the threshold) is burned. Repeatable indefinitely → denial of service of the spend pipeline, mirroring the Allo `_distribute` revert.

The script check in `multisig` (`send.rs:277`) binds the prevout's *script* to the offset key but checks neither the `outpoint`'s existence nor the `value`'s correctness.

### Impact Explanation
A party able to feed corrupted bytes into `ReceivedOutput::read` (or otherwise supply a `ReceivedOutput` whose `value` doesn't match chain state) can cause the threshold group to sign a transaction that (a) permanently donates the value discrepancy to miners, or (b) is unbroadcastable — a repeatable DoS where each attempt consumes a fresh FROST signing session. This matches the report's shape: a credited amount divorced from the real balance poisons downstream transfer math.

### Likelihood Explanation
Reachability depends on `ReceivedOutput` bytes being attacker-influenceable before reaching `SignableTransaction::new`. In honest scanner flow (`Scanner::scan_transaction`, `mod.rs:199`) values come from confirmed chain data, so this requires an untrusted-bytes path into `read` (explicitly in scope) or a storage/relay compromise. The understated-value variant yields deterministic, permanent fund loss bounded by the discrepancy; the overstated variant yields deterministic DoS. Medium severity is appropriate — real loss/DoS, but gated on the input vector.

### Recommendation
Bind `ReceivedOutput` to verified chain state: when constructing `SignableTransaction` (or in `multisig`), fetch each `prevout` via the outpoint and compare `value` and `script_pubkey` against the on-chain UTXO — i.e., "increase the pool amount by the post-transfer balance," per the original recommendation. At minimum, `TransactionSignMachine::sign` should fail if `prevouts[i]` doesn't match the referenced outpoint's real `TxOut`.

### Proof of Concept
```rust
// Obtain a genuinely-received output, then corrupt its claimed value
let real: ReceivedOutput = scanner.scan_transaction(&funding_tx).remove(0);
let mut bytes = real.serialize();
// Layout: offset (32B) || TxOut { value: u64le, script_len, script } || outpoint
// Understate the value by `skim` satoshis
let v_off = 32;
let claimed = u64::from_le_bytes(bytes[v_off..v_off + 8].try_into().unwrap());
bytes[v_off..v_off + 8].copy_from_slice(&(claimed - skim).to_le_bytes());
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// Build a spend: change is computed from the understated sum
let stx = SignableTransaction::new(
    vec![forged], &payments, Some(change_script), None, fee_per_vbyte,
).unwrap();
// stx.needed_fee() reflects only fee_per_vbyte * vbytes, but the signed
// transaction's actual fee (send.rs:139) = real_input - outputs
//                                          = needed_fee + skim
// → `skim` sats are irrevocably paid to miners once broadcast.
```
For the DoS variant, set the value *above* the real UTXO (or invent the `OutPoint`): `multisig` still accepts it (only `script_pubkey` is checked, `send.rs:277`), `sign` produces shares over `Prevouts::All(&[forged_txout])` (`send.rs:375-390`), and the completed transaction is invalid — no signature can verify against mismatched prevout amounts.