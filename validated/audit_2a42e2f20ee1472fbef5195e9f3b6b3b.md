### Title
Zero-value / dust outputs scanned as spendable inputs cause transaction-building failure and fee drain - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
The Cooler bug class is an operation executed unconditionally even when the computed delta is zero, causing a revert/DoS. The Serai analog lives in `Scanner::scan_transaction` / `scan_block`: every output whose `script_pubkey` matches a registered script is recorded as a `ReceivedOutput` with no minimum-value filter, and `SignableTransaction::new` later consumes all supplied `inputs` without checking that each input's value exceeds the marginal fee it adds.

### Finding Description
`Scanner::scan_transaction` pushes a `ReceivedOutput` for every transaction output matching `self.scripts`, regardless of `output.value` (`networks/bitcoin/src/wallet/mod.rs:199-214`). The only key into `scripts` is the `script_pubkey` (`mod.rs:205`), so any unprivileged party who knows the multisig's P2TR address can craft a transaction paying a dust (or zero-value, which is consensus-valid though non-standard) output to it.

Downstream, `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) enforces `DUST` on *payments* (`send.rs:165-169`) and on the *change* output (`send.rs:229`), but performs no equivalent check on *inputs*. Every `ReceivedOutput` passed in is unconditionally turned into a `TxIn` (`send.rs:177-185`), increasing `input_sat` by its value and increasing `weight`/`vbytes`/`needed_fee` via `calculate_weight_vbytes` (`send.rs:62-127`, `204-206`). A Taproot input adds ~58 vB; an input worth less than `fee_per_vbyte * 58` is a net liability, and enough such inputs make `input_sat < payment_sat + needed_fee`, forcing `TransactionError::NotEnoughFunds` (`send.rs:215-221`) — a DoS on transaction construction exactly like the zero-transfer revert in Cooler.

### Impact Explanation
- **DoS on signing:** an attacker floods the multisig with dust-value outputs to its script_pubkey. When these are included as inputs, `SignableTransaction::new` returns `NotEnoughFunds`, stalling legitimate spends until inputs are filtered elsewhere.
- **Fee drain / unspendable funds:** outputs worth less than their marginal spending fee are reported as received funds that are economically unspendable — spending them burns more than they contribute.
- The asymmetry is the bug: value is validated on every output the transaction creates, but never on the untrusted inputs it consumes.

### Likelihood Explanation
Any party who observes the multisig's P2TR `script_pubkey` (public on-chain once used) can send dust outputs to it at minimal cost. No validator collusion, leaked keys, or malicious peer is required — the trigger is an ordinary Bitcoin transaction the attacker broadcasts themselves, matching the "Bitcoin transactions they send" reachability rule. The defect is deterministic: `scan_transaction` has no `output.value >= DUST` (or fee-worthiness) gate.

### Recommendation
Mirror the Cooler fix ("transfer collateral only when amount>0"): only record an output in `scan_transaction` when `output.value.to_sat() >= DUST` (and ideally only when its value exceeds the marginal fee an additional input adds at the current fee rate), and/or have `SignableTransaction::new` skip or reject inputs whose value is below the marginal input weight fee before summing `input_sat`.

### Proof of Concept
1. Register/observe the multisig P2TR script via `Scanner::new(key)` / `register_offset`; the script is inserted into `scripts` keyed only by `script_pubkey` (`mod.rs:163-165`, `180-196`).
2. Attacker broadcasts a Bitcoin transaction with an output `{ value: 1 sat (or < 546), script_pubkey: multisig_p2tr }`. Consensus permits sub-dust/zero-value outputs; relay policy is the only barrier and dust outputs are standard.
3. `scan_transaction` emits `ReceivedOutput { offset, output, outpoint }` for it (`mod.rs:205-211`) — no value check exists between `mod.rs:201` and `mod.rs:211`.
4. When the scheduler passes these `ReceivedOutput`s to `SignableTransaction::new`, each is converted into a `TxIn` (`send.rs:177-185`), raising `vbytes` and `needed_fee` (`send.rs:204-206`) while contributing ~nothing to `input_sat` (`send.rs:175`).
5. With enough dust inputs (or a legitimately thin balance), `input_sat < payment_sat + needed_fee` triggers `Err(TransactionError::NotEnoughFunds { .. })` (`send.rs:215-221`) — transaction construction fails unconditionally, the direct analog of Cooler's unconditional zero-amount collateral transfer reverting.