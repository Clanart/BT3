### Title
OP_RETURN data output is excluded from weight/vsize accounting, producing under-fee transactions — (`networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` adds an `OP_RETURN` output carrying caller-supplied `data` to the transaction's output list, but computes the transaction's weight, vbytes, and `needed_fee` from a template transaction that only contains the payment outputs. The serialized size of the OP_RETURN output (8-byte value + script length + `OP_RETURN` + push opcode + up to 80 bytes of data) is never counted, so the fee actually paid is `fee_per_vbyte` applied to a smaller vsize than the real transaction. The same omission affects the `MAX_STANDARD_TX_WEIGHT` check.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` before the size calculation, yet the size calculation is performed with `payments` only:

```rust
// networks/bitcoin/src/wallet/send.rs
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` reconstructs a template `Transaction` whose outputs are built exclusively from `payments` (plus optional `change`), so any `data` output contributes zero to `weight`, `vbytes`, and therefore `needed_fee` (`send.rs` lines 85–99, 204–206). The change-path recalculation at lines 225–232 has the same defect. The `TooLowFee` check (lines 211–213) and the weight cap (line 241) both validate the under-counted values, while the real transaction serialized into `self.tx` (lines 246–251) includes the full OP_RETURN output.

An unprivileged party can trigger this: Serai's InInstruction flow lets any Bitcoin sender embed arbitrary `data` (up to the 80-byte cap enforced at line 171) that the coordinator attaches to a spend via this exact `SignableTransaction::new` path.

### Impact Explanation
The resulting transaction pays `input_sat - output_sat = needed_fee`, but its true vsize is larger than `vbytes` by roughly `(9 + push overhead + data.len())` bytes (up to ~90 vB). Two concrete consequences:

- **Effective fee rate below the requested rate and potentially below `DEFAULT_MIN_RELAY_TX_FEE`.** When `data` is large and `fee_per_vbyte` is near the minimum, the realized fee rate can drop under the mempool minimum relay fee, so the signed transaction is rejected by the Bitcoin network even though `TooLowFee` passed. Funds in the multisig inputs cannot be moved by that transaction — received funds are effectively unspendable via the produced plan.
- **Standardness check bypass.** A transaction near `MAX_STANDARD_TX_WEIGHT` plus a maximal OP_RETURN output can exceed the standardness limit while passing the check at line 241, again yielding a valid-but-unrelayable spend.

### Likelihood Explanation
Triggering requires only that a spend carries a `data` payload — which any external user can cause via an InInstruction with a large payload — combined with a fee rate or size margin small enough that the ~90 vB discrepancy matters. The bug is deterministic and requires no privileged position, no collusion, and no timing race; every `data`-carrying `SignableTransaction` under-pays relative to its stated `fee_per_vbyte`. Whether it crosses the relay-fee or standardness threshold depends on parameters, making the confidentiality-free impact a correctness/availability-of-funds issue of moderate severity.

### Recommendation
Include the data output in the size accounting: pass the full intended output set (payments + OP_RETURN + optional change) to `calculate_weight_vbytes`, or construct the template `tx_outs` first and derive `weight`/`vbytes` from it. Concretely, build the OP_RETURN `TxOut` before line 204 and compute sizes over `payments` plus the OP_RETURN script, e.g. by extending `calculate_weight_vbytes` to take `&[TxOut]`-equivalent script/value pairs so the template always mirrors `tx.output` exactly.

### Proof of Concept
1. Call `SignableTransaction::new(inputs, payments, None, Some(vec![0u8; 80]), fee_per_vbyte)` with `fee_per_vbyte` chosen so `needed_fee == DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` exactly.
2. The returned `SignableTransaction.tx` contains an extra ~90-byte OP_RETURN output not reflected in `vbytes`.
3. `tx.fee() / real_vsize < DEFAULT_MIN_RELAY_TX_FEE / 1000`, so the transaction the FROST `TransactionMachine` signs cannot be relayed, while `TransactionError::TooLowFee` was not raised — demonstrating fee/standardness accounting inconsistent with the transaction actually produced at `networks/bitcoin/src/wallet/send.rs` lines 194–206 and 246–251.