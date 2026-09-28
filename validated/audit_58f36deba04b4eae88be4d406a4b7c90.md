### Title
`SignableTransaction` trusts the unverified `value` inside `ReceivedOutput` — only the `script_pubkey` is checked against the offset key, so an inflated output produces change/fee math and signatures over phantom funds - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
Analogous to the Tokemak router charging users without crediting already-deposited ETH, `bitcoin-serai`'s `SignableTransaction` builds payments, fees, and change from the *claimed* value carried inside each `ReceivedOutput`, while `SignableTransaction::multisig` validates only that the prevout's `script_pubkey` equals the script derived from the supplied scalar offset — it never verifies the `value` field (and cannot, since `ReceivedOutput::read` accepts it verbatim from untrusted bytes). The ledger between "value claimed" and "value actually on chain" is never reconciled.

### Finding Description
`ReceivedOutput::read` deserializes the offset, `TxOut` (which embeds `value`), and outpoint directly from a byte stream with no consistency check against the referenced on-chain output (`networks/bitcoin/src/wallet/mod.rs:120-134`). `SignableTransaction::new` then:

- sums `input.output.value` into `input_sat` (`send.rs:175`),
- checks `input_sat >= payment_sat + needed_fee` against that fabricated sum (`send.rs:215`),
- computes the change output as `input_sat - payment_sat - fee_with_change` (`send.rs:228-230`).

The only authenticity check occurs later in `multisig`, which requires `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` (`send.rs:276-279`) — the script is verified, the amount is not. Signing uses `Prevouts::All(&self.tx.prevouts)` (`send.rs:375`), so the fabricated amounts are committed into each BIP-341 sighash.

An unprivileged party who can feed crafted `ReceivedOutput` bytes (via `ReceivedOutput::read`, reachable through `Output::read` in `processor/src/networks/bitcoin.rs:156`) supplies an output with a script that legitimately belongs to the vault offset (passing `multisig`) but an inflated `value`. The constructed transaction then:

- declares a change output worth more than the real inputs hold (`value >= DUST` check passes on phantom funds), and
- produces Schnorr signatures committing to prevout amounts that do not match the chain — the resulting transaction is unspendable/invalid on broadcast.

### Impact Explanation
Like the original report's "user charged more than necessary and the extra WETH stuck in the router," phantom input value is credited into real outputs: change is minted out of thin air in the transaction's own accounting, and the fee/change split is computed over funds that do not exist. The signed transaction commits to false prevout amounts, so every signature is invalid against the real UTXOs — the burn/spend plan stalls and any internal accounting that recorded the inflated change as recouped funds reports value that is not spendable. Repeated poisoned inputs can keep the coordinator producing invalid transactions.

### Likelihood Explanation
The vulnerable state is reached purely through deserialization of an attacker-influenced `ReceivedOutput` — a listed untrusted input surface. The offset/script check in `multisig` gives a false sense of validation while leaving `value` and `outpoint` attacker-controlled, and `new` performs arithmetic on those fields before any key check occurs.

### Recommendation
- In `SignableTransaction::new` / `multisig`, bind each `ReceivedOutput` to its real UTXO: have callers supply verified outputs (as the scanner produces) or store/verify the outpoint→`TxOut` mapping via the RPC/`Prevouts` source rather than trusting the serialized `value`.
- At minimum, validate in `multisig` that `prevouts[i]` equals the actual output referenced by `tx.input[i].previous_output`, and treat `ReceivedOutput::read` output as untrusted until confirmed against chain state.
- Recompute change only over verified input value, so overstated inputs cannot inflate outputs.

### Proof of Concept
1. Craft bytes for `ReceivedOutput::read`: `offset = o` (a registered vault offset), `output = TxOut { script_pubkey: p2tr_script_buf(key + G*o), value: 10 * real_value }`, any valid-format `outpoint`.
2. Call `SignableTransaction::new(vec![crafted], payments, Some(change), None, fee_per_vbyte)`. The `input_sat` sum (send.rs:175), `NotEnoughFunds` check (send.rs:215), and change computation `input_sat - payment_sat - fee_with_change` (send.rs:228) all use the inflated `10 * real_value`; a change output crediting phantom sats is emitted.
3. `multisig(&keys)` succeeds because `p2tr_script_buf(keys.offset(o).group_key()) == prevouts[0].script_pubkey` (send.rs:276-279) — the value is never compared to anything.
4. `TransactionSignMachine::sign` hashes `Prevouts::All` containing the fake amount (send.rs:375-390); the completed transaction's signatures commit to nonexistent value and the transaction (and its overvalued change output) is invalid on chain.