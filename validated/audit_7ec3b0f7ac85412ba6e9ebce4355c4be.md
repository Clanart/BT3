### Title
`SignableTransaction::new` computes weight/vbytes without the caller-supplied OP_RETURN output, underpaying the fee - ([File: networks/bitcoin/src/wallet/send.rs](https://github.com/Annirich/serai--025/blob/main/networks/bitcoin/src/wallet/send.rs))

### Summary
The bug class is "a hard-coded/constant assumption inside a formula whose operands actually vary in size." In `Pool.sol` it was a fixed `1e18` scaling factor; in `bitcoin-serai` it is a fixed output set: `SignableTransaction::new` adds a caller-controlled OP_RETURN output to `tx_outs` (lines 194-202), but computes the transaction weight and virtual size via `calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204), which only accounts for `payments` — the data output is never included. `needed_fee` and the minimum-relay-fee check are therefore computed over a smaller transaction than the one actually produced and signed.

### Finding Description
`SignableTransaction::new` accepts a `data: Option<Vec<u8>>` argument of up to 80 bytes (checked at lines 171-173). When present, an OP_RETURN `TxOut` is pushed into `tx_outs` at lines 194-202. The fee estimation then runs:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
```

`calculate_weight_vbytes` (lines 62-127) builds a mock transaction containing only `inputs` and `payments` (plus optionally `change`). The OP_RETURN output — roughly `1 (count) + 1 (script len) + ~1-2 (push opcode) + data.len()` bytes of non-witness data, i.e. up to ~90+ extra vbytes for 80 bytes of data — is omitted from both `weight` and `vbytes`. The same omission applies to the `weight_with_change`/`vbytes_with_change` recomputation at lines 225-226. Consequently:

- `needed_fee` is too low by `fee_per_vbyte * (~10 + data.len() + output overhead)` satoshis.
- The `TooLowFee` check at line 211 can pass while the *real* transaction is below `DEFAULT_MIN_RELAY_TX_FEE`, so a `Ok(SignableTransaction)` is returned for a transaction that will not relay under default policy.
- The `MAX_STANDARD_TX_WEIGHT` check at line 241 uses `weight` that excludes the data output; while the data cap (80 bytes) makes exceeding 400,000 WU unlikely, the check is still performed against an incorrect value.

The signed transaction commits to all outputs via `Prevouts::All` at lines 373-390, so the under-sized fee is baked into the actual broadcast transaction — the fee paid is `sum(inputs) - sum(outputs)` which equals the underestimated `needed_fee` (lines 132-141).

### Impact Explanation
A transaction created with `data` pays a lower effective fee rate than the caller requested, and can fall below the default minimum relay fee despite passing the `TooLowFee` guard. The produced transaction will be rejected by relaying nodes or sit unconfirmed, leaving the spent `ReceivedOutput`s locked until a replacement is signed — funds committed to a transaction that is not spendable/relayable as constructed. All signers produce signatures for a transaction whose fee does not match the requested `fee_per_vbyte`.

### Likelihood Explanation
Reachable whenever `SignableTransaction::new` is called with `data: Some(_)`, which is a public, caller-supplied input to this API (the constructor explicitly supports up to 80 bytes). The bug triggers deterministically — it is not probabilistic — and the severity of the underpayment grows with `data.len()` and `fee_per_vbyte`. Whether it crosses the min-relay boundary depends on how close `fee_per_vbyte` is to the minimum, but any use of `data` silently yields a lower feerate than requested.

### Recommendation
Include the OP_RETURN output in the size estimation. Either pass the data-bearing `TxOut` into `calculate_weight_vbytes` (e.g., extend `payments` handling to accept the full `tx_outs` list, or add a `data: Option<&[u8]>` parameter that pushes the same `TxOut` into the mock transaction), for both the no-change and with-change computations at lines 204 and 225-226.

### Proof of Concept
```rust
// networks/bitcoin context
let data = vec![0u8; 80];
let tx = SignableTransaction::new(
    vec![input],                 // ReceivedOutput with sufficient value
    &[(payment_script, 10_000)], // one payment
    None,                        // no change
    Some(data.clone()),          // OP_RETURN output
    fee_per_vbyte,               // e.g. 10 sat/vb
).unwrap();

// The real transaction contains an extra ~91-vbyte output, but:
// needed_fee() == fee_per_vbyte * vbytes(without the OP_RETURN output)
let real_vsize = /* vsize of tx.transaction() including the OP_RETURN output */;
assert!(tx.needed_fee() < real_vsize * fee_per_vbyte);
// If needed_fee / real_vsize < 1 sat/vb, the tx is below DEFAULT_MIN_RELAY_TX_FEE
// despite TooLowFee not having been returned.
```
Concretely: with `fee_per_vbyte = 2`, an 80-byte `data` output adds ~91 uncounted vbytes, so `needed_fee` is ~182 sat short; the resulting transaction's true feerate is ~1.7 sat/vb or lower, and can drop under the 1 sat/vb minimum relay threshold while the constructor returns `Ok`.