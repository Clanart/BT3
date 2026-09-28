### Title
Unspendable dust outputs are reported as received funds by `Scanner::scan_transaction` and accepted as transaction inputs by `SignableTransaction::new`, letting a griefer pollute the vault UTXO set - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The analog of the dust-execution grief is in bitcoin-serai's output scanning and transaction building. `Scanner::scan_transaction` returns any `TxOut` matching a registered `script_pubkey` as a `ReceivedOutput` with no minimum-value check, and `SignableTransaction::new` accepts every supplied `ReceivedOutput` as an input regardless of whether its value covers the marginal fee cost of spending it. An unprivileged party who knows a vault/forward address can send dust-valued outputs to it, producing UTXOs that are reported as received yet are unprofitable or even impossible to spend, and that permanently inflate or fail any transaction they're included in.

### Finding Description
`Scanner::scan_transaction` (networks/bitcoin/src/wallet/mod.rs, lines 199–214) iterates `tx.output`, looks up `output.script_pubkey` in `self.scripts`, and pushes a `ReceivedOutput` for every match with no check on `output.value`. A `ReceivedOutput` carries only `offset`, `output`, and `outpoint` (lines 90–97), and `ReceivedOutput::read` (lines 122–134) similarly performs no value validation — the raw `TxOut` is consensus-decoded and accepted.

Downstream, `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs, lines 150–256) enforces `DUST = 546` only on *payments* (lines 165–169) and on the *change* output (line 229). Inputs are summed blindly: `input_sat` is the sum of all `input.output.value` (line 175), each input contributes a fixed ~58-vbyte weight via `calculate_weight_vbytes` (lines 68–127), and the aggregate check `input_sat < payment_sat + needed_fee` (line 215) treats dust and real UTXOs identically. A dust input whose value is less than `fee_per_vbyte * 58` is strictly fee-negative: including it reduces the spendable value of the transaction. If a dust input is bundled with real UTXOs to fund payments, the transaction either overpays fees, fails with `NotEnoughFunds`, or — if the scheduler retries with the same polluted set — repeatedly fails signing attempts.

### Impact Explanation
Two accepted-impact shapes apply:

1. **Funds reported received that are not spendable.** A 1–545 sat output to a registered `script_pubkey` is returned by `scan_transaction`/`scan_block` as a `ReceivedOutput` indistinguishable from a real deposit. Its value is below the relay/dust threshold and below the fee cost of spending it, so it can never be profitably spent — yet it is indistinguishable from credited balance at the library level.

2. **Griefing of legitimate transactions.** Since inputs carry no dust guard, a dust UTXO included among `inputs` consumes `previous_output` weight budget and reduces `input_sat` available for payments. A griefer who dusts the vault address creates outputs that any coin-selection that naively takes all scanned outputs will drag into every `SignableTransaction`, degrading or blocking withdrawals for the cost of a few hundred sats — the same "cheap dust poisons everyone's transaction" structure as the reference report.

### Likelihood Explanation
The attacker needs only a Bitcoin transaction paying to a publicly known vault/forward address — no validator status, no keys, no collusion. Sending dust to a known Taproot output is trivially cheap and permissionless. Whether the dust is ever *included* as a `SignableTransaction` input depends on the caller's input selection (processor-side scheduling does filter `N::DUST` after scanning, but that filtering lives outside the in-scope crate and relies on the caller remembering to apply it); the library itself provides no protection and reports the output as spendable.

### Recommendation
- Enforce a minimum-value check in `Scanner::scan_transaction` (e.g. skip outputs with `value < DUST`) so dust is never surfaced as a `ReceivedOutput`, matching the `DUST` constant already defined in `send.rs`.
- In `SignableTransaction::new`, reject or skip inputs whose `output.value` is below the marginal fee they introduce (`fee_per_vbyte *` per-input vbytes), so fee-negative inputs can never be committed to a sighash.
- Validate value on `ReceivedOutput::read` as defense-in-depth for deserialized outputs.

### Proof of Concept
```rust
// networks/bitcoin context (conceptual)
// Given a Scanner registered for key K, an attacker broadcasts a tx with:
//   TxOut { value: Amount::from_sat(1), script_pubkey: p2tr_script_buf(K).unwrap() }
let outputs = scanner.scan_transaction(&attacker_tx);
assert_eq!(outputs.len(), 1);           // dust reported as a received, "spendable" output
assert_eq!(outputs[0].value(), 1);

// Spending it is fee-negative: the input adds ~58 vbytes but only 1 sat
let dust_input = outputs[0].clone();
let res = SignableTransaction::new(
  vec![real_utxo, dust_input],           // coin selection including the dust
  &[(payment_script, payment_amount)],
  Some(change_script),
  None,
  fee_per_vbyte,
);
// Either NotEnoughFunds (payment blocked) or the tx pays
// fee_per_vbyte * 58 more in fees than the 1 sat the input contributes.
```

Uncertainty note: the severity hinges on the caller's input-selection policy. The processor-level scanner does drop outputs below `N::DUST` (processor/src/multisigs/scanner.rs:564), which mitigates the deposit-reporting half in that integration, but nothing in the in-scope crate prevents a dust `ReceivedOutput` from being constructed, read, or spent.