### Title
`ReceivedOutput` amount trusted for fee math and `Prevouts::All` sighash without consistency check — fabricated or stale amounts lock funds / burn value as fees - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
The escrow bug class is: a fixed amount recorded at creation (`i_arbiterFee`) is later treated as authoritative against a balance that can differ, and the mismatch bricks resolution and locks funds. `networks/bitcoin` has the same shape: `ReceivedOutput.output.value` is a fixed, caller/deserialization-supplied amount that `SignableTransaction` treats as the true input balance for all fee/change arithmetic and for the `Prevouts::All` sighash commitment, with no check that it matches the actual on-chain UTXO. Because `ReceivedOutput::read` accepts arbitrary bytes (`networks/bitcoin/src/wallet/mod.rs:122-134`), an unprivileged party able to feed a `ReceivedOutput` into `SignableTransaction::new` can make the wallet sign a transaction over amounts that do not match reality.

### Finding Description
`Scanner::scan_transaction` clones the real `TxOut` (`mod.rs:205-210`), so scanner-derived outputs are accurate. However `ReceivedOutput` is also constructible from untrusted bytes via `ReceivedOutput::read`, which decodes an arbitrary `TxOut` (including its `value`) and `OutPoint` with no validation that the pair exists on-chain or carries that amount.

`SignableTransaction::new` then uses the recorded amounts as ground truth:

- `input_sat` is summed from `input.output.value` (`send.rs:175`).
- `prevouts` are stored verbatim (`send.rs:253`) and later committed in the sighash via `Prevouts::All(&self.tx.prevouts)` (`send.rs:375`), so each `taproot_key_spend_signature_hash` commits to the *claimed* prevout amount and scriptPubKey.
- `multisig` verifies only that `prevouts[i].script_pubkey` matches the offset key (`send.rs:277`); it never checks `prevouts[i].value` against anything.

Two consequences mirror the escrow report:

1. **Overstated value** (`claimed > actual`): `input_sat` is inflated, so `SignableTransaction::new` approves payments/change the real UTXO cannot fund (`send.rs:215`, `send.rs:228-234`). The signed transaction commits to prevout amounts that do not match the real UTXOs, so BIP-341 sighash verification fails and the transaction is consensus-invalid. Analogous to `totalFee > tokenBalance` reverting: the inputs consumed by the plan cannot be spent by this transaction, and if the `SignableTransaction`/outpoints were persisted the associated funds are stuck until the plan is rebuilt — a permanent lock if the forged record is what was stored.

2. **Understated value** (`claimed < actual`): change is computed against the understated `input_sat` (`send.rs:228`), so the difference between the real UTXO value and the outputs is folded into the fee (`fee() = sum(inputs) - sum(outputs)`, `send.rs:138-141`). The transaction is valid and broadcasts successfully, but the excess real value is irrevocably paid to miners — loss of funds, not recoverable.

The `offsets`/`prevouts` vectors are built in input order (`send.rs:176-185`), so a `ReceivedOutput` carrying a valid `outpoint` and matching `script_pubkey` but a falsified `value` passes every check in `new` and `multisig`.

### Impact Explanation
Funds controlled by the threshold key can be either locked (invalid sighash commitment makes the transaction unspendable and stalls the plan's inputs) or destroyed (excess real value silently converted into miner fees when the recorded amount understates the UTXO). This matches the accepted impact classes "funds reported received that are not spendable" and unintended signing: the multisig signs a `Prevouts::All` commitment over attacker-chosen amounts.

### Likelihood Explanation
The reachability surface is exactly the sanctioned one: untrusted bytes fed to `ReceivedOutput::read` (`mod.rs:122`), or any pipeline that persists/reloads `ReceivedOutput`s from storage an attacker can influence. No malformed curve points, malicious validators, or leaked keys are needed — only a forged serialized output. The serde path performs zero sanity checks (no minimum value, no outpoint-vs-value consistency possible locally), so the bug is deterministic once such bytes reach `SignableTransaction::new`.

### Recommendation
- When constructing `SignableTransaction`, verify each `ReceivedOutput`'s claimed `value` and `script_pubkey` against the confirmed on-chain UTXO (e.g., re-fetch via RPC `get_tx_out`/`get_transaction`) before computing `input_sat` or before signing, not just the script-key check in `multisig`.
- At minimum, treat `ReceivedOutput::read` output as untrusted: mark deserialization results as unconfirmed and require scanner/RPC confirmation of `(outpoint, value, script_pubkey)` before inclusion in a signing plan.
- As defense in depth, cap `fee()`/`needed_fee` divergence: reject transactions where `fee()` exceeds `needed_fee` by more than the change-dust margin (`send.rs:133-141`), which would catch understated inputs.

### Proof of Concept
1. Obtain a real UTXO paying the group's (possibly offset) key: `OutPoint` `O`, script `S`, real value `V`.
2. Craft bytes for `ReceivedOutput::read`: `offset || serialize(TxOut{value: V + Δ, script_pubkey: S}) || serialize(O)`.
3. Feed the resulting `ReceivedOutput` into `SignableTransaction::new` with payments totaling `V + Δ - needed_fee` (passes the `NotEnoughFunds` check at `send.rs:215` against inflated `input_sat`).
4. Run `multisig`/`preprocess`/`sign`/`complete` with the threshold group. The produced transaction commits to prevout amount `V + Δ` under `Prevouts::All` (`send.rs:375`); nodes reject it because the real amount is `V` — the input cannot be spent by this transaction (funds locked / plan bricked, analog of `resolveDispute` reverting).
5. Variant: use `value: V - Δ`. The transaction is valid; `fee()` exceeds `needed_fee` by `Δ`, permanently burning `Δ` satoshis of group funds to miners.