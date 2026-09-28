### Title
ReceivedOutput's recorded TxOut value is trusted over the on-chain amount, so forged/stale values produce permanently invalid transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the Teller escrow bug — where a stale `collateral._amount` recorded at deposit time is used instead of the token's actual balance — `SignableTransaction`/`TransactionSignMachine` trust the `value` field recorded inside `ReceivedOutput` rather than the output's actual on-chain amount. `ReceivedOutput::read` accepts a fully attacker-controlled `TxOut` (`networks/bitcoin/src/wallet/mod.rs:122-134`), and nothing ever verifies that the recorded value matches the UTXO on chain.

### Finding Description
In `SignableTransaction::new`, input value, fee, and change math are computed entirely from `input.output.value.to_sat()` (`send.rs:175`, `send.rs:228-230`). In `SignableTransaction::multisig`, the only sanity check is that `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` (`send.rs:276-279`) — the *amount* is never checked. Finally, `TransactionSignMachine::sign` commits to `Prevouts::All(&self.tx.prevouts)` in the BIP-341 sighash (`send.rs:373-386`), which binds the recorded amount into the signed message.

Two failure modes, mirroring "rebase up / rebase down":

- **Recorded value > actual value (rebase up analog):** `input_sat` is inflated, so `checked_sub(payment_sat + fee_with_change)` mints a change output worth more than the real inputs provide. The resulting transaction is consensus-invalid (`outputs > inputs`) and can never be broadcast.
- **Recorded value < actual value (rebase down analog):** BIP-341 commits each input's amount into the sighash. The threshold signature is produced over a sighash computed with the wrong amount, so every node's verification of the finalized transaction fails — the signed transaction is unbroadcastable and the fee budget/change are also wrong.

Like the escrow bug, the caller has no way to specify the correct amount at spend time: the recorded `TxOut` is the only source of truth, and `multisig()`'s script check passes regardless of the amount.

### Impact Explanation
Any `ReceivedOutput` whose serialized bytes were corrupted or forged (the type is explicitly deserializable from untrusted input via `ReceivedOutput::read`) causes the wallet to generate transactions that are permanently invalid — either rejected at sighash verification or consensus-invalid due to inflated change. Funds are reported as received/spendable but cannot actually be spent through this code path, matching the "asset will not be withdrawable" impact.

### Likelihood Explanation
Reachability requires an untrusted party to influence the `ReceivedOutput` bytes consumed by `SignableTransaction::new` (e.g., outputs relayed between components in serialized form). `register_offset`-derived script checking means the attacker cannot redirect the funds to themselves — only corrupt the amount — so this is an integrity/availability issue rather than theft, consistent with Medium severity.

### Recommendation
Verify the prevout amount against chain data before signing: in `SignableTransaction::new` or `multisig`, fetch each `outpoint`'s actual `TxOut` (via RPC) and reject `ReceivedOutput`s whose recorded `value`/`script_pubkey` diverge. At minimum, in `multisig`, recompute and assert `prevouts[i].value` equals the on-chain amount for `input[i].previous_output`, so a stale recorded amount is caught before preprocess/sign rather than producing an unbroadcastable transaction — the equivalent of reading `balanceOf` at withdrawal time instead of trusting the stored `_amount`.

### Proof of Concept
```rust
// Conceptual: deserialize a ReceivedOutput where value is corrupted
let mut output: ReceivedOutput = scanner.scan_transaction(&tx).swap_remove(0);
let mut bytes = output.serialize();
// Corrupt the TxOut value field (first 8 bytes of the TxOut consensus encoding,
// after the 32-byte offset): set it to value+1 (rebase-up analog)
bytes[32 + 7] = bytes[32 + 7].wrapping_add(1); // high byte of u64 LE amount
let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// multisig() passes: script_pubkey unchanged, offset still valid
let tx = SignableTransaction::new(vec![forged], &payments, Some(change), None, FEE).unwrap();
let signed = sign(&keys, &tx);
// signed is unbroadcastable: change output exceeds real input sum
// (consensus-invalid), or with value-1 the BIP-341 sighash commits a wrong
// amount and the signature fails verification on chain.
```
The trust of the recorded value flows through `send.rs:175` (input sum), `send.rs:228-230` (change computed from recorded value), and `send.rs:375` (`Prevouts::All` commits recorded amounts into the sighash).