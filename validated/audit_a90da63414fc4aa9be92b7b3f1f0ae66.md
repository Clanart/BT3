### Title
Fee/vsize estimation excludes the OP_RETURN `data` output, so `SignableTransaction` underpays relative to `fee_per_vbyte` — (`networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` adds an extra `OP_RETURN` output when `data` is provided, but the weight/vbyte (and therefore fee) calculation is performed over `payments` only — a slice that does not contain the `OP_RETURN` output. This is the same bug class as M-10 (a fee computed over the wrong base amount in one code path while other paths use the full amount): the "with data" path charges the fee on a strictly smaller virtual size than the transaction actually has.

### Finding Description
In `new`, the OP_RETURN output is pushed onto `tx_outs` before the size estimation:

- `tx_outs.push(TxOut { value: ZERO, script_pubkey: OP_RETURN(data) })` at `send.rs` L194–202.
- The weight/vbyte estimate is then computed with `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at `send.rs` L204, and the change-aware variant with `payments` at `send.rs` L225–227.

`calculate_weight_vbytes` (L62–99) builds a probe `Transaction` whose `output` is derived solely from the `payments` slice plus the optional `change` script. The OP_RETURN output is never included. Consequently `weight`, `vbytes`, `needed_fee = fee_per_vbyte * vbytes` (L206), the `TooLowFee` relay check (L211), and the change computation `input_sat.checked_sub(payment_sat + fee_with_change)` (L228) all assume a transaction that is smaller than the one actually constructed in the returned `SignableTransaction` (L245–255), which contains `payments + OP_RETURN [+ change]` outputs.

The same inconsistency does not exist for `payments` or `change`, which are correctly accounted — only the `data` path uses an incomplete base for the fee/size formula.

### Impact Explanation
Every `SignableTransaction` created with `data != None` carries a real vsize strictly larger than estimated, so:

- The effective fee rate is always below the requested `fee_per_vbyte` (an OP_RETURN output adds ~11 + len + script overhead bytes, up to ~100 vbytes at the 80-byte data limit).
- The `TooLowFee` minimum-relay-fee check can pass while the final transaction is actually below `DEFAULT_MIN_RELAY_TX_FEE`, producing a consensus-valid transaction that default mempool policy will not relay or confirm — outputs spent by it are effectively unspendable until the wallet reconstructs the transaction with corrected parameters.
- When change is included, `value = input_sat - payment_sat - fee_with_change` assigns too much to the change output (because `fee_with_change` is too small), further lowering the true fee below the intended rate and compounding the relay failure.

Because signing commits to the exact transaction (`Prevouts::All` in `TransactionSignMachine::sign`, L373–390), participants produce a threshold signature over an underpaying transaction; the signed tx cannot be fixed without re-running the ceremony.

### Likelihood Explanation
Deterministic: any caller passing non-empty `data` triggers the underestimation; no attacker action or race is needed. Whether it is merely a reduced fee rate or an outright relay failure depends on how close `fee_per_vbyte` is to the mempool minimum, but the fee-rate deviation occurs on every use of the feature. Reachable by any unprivileged party able to influence the `data` argument of transaction construction.

### Recommendation
Include the OP_RETURN output in the size estimate — e.g., perform `calculate_weight_vbytes` over `&tx_outs` (after the OP_RETURN push) rather than `payments`, and pass the same output set to the `change` variant; or equivalently, append a probe `TxOut` for the OP_RETURN inside `calculate_weight_vbytes`. This matches how `buyQuote`/`sellQuote`/`changeFeeQuote` were made consistent in the reference finding.

### Proof of Concept
```rust
// In SignableTransaction::new (send.rs):
// 1. OP_RETURN output added to the real tx:
tx_outs.push(TxOut { value: Amount::ZERO,
  script_pubkey: ScriptBuf::new_op_return(data) });            // L194-202

// 2. Fee estimated WITHOUT that output:
let (mut weight, vbytes) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, None); // L204
let mut needed_fee = fee_per_vbyte * vbytes;                   // L206

// 3. Change also computed against the wrong base:
let (_, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change)); // L225-226
let fee_with_change = fee_per_vbyte * vbytes_with_change;               // L227
let value = input_sat - (payment_sat + fee_with_change);                // L228

// Result: final tx has outputs = payments + OP_RETURN (+ change),
// while needed_fee covers only payments (+ change).
// needed_fee / actual_vbytes < fee_per_vbyte, possibly below
// DEFAULT_MIN_RELAY_TX_FEE -> transaction built and signed by the
// TransactionMachine path is not relayed despite TooLowFee passing.
```