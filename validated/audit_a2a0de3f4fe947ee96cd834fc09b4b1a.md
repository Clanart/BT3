### Title
`SignableTransaction::new` omits the OP_RETURN data output from the fee/weight calculation, so the constructed transaction pays a lower fee than `needed_fee()` reports and may fall below the minimum relay fee — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The GMX report describes a mismatch between the amount a payer debits and the amount the payee is credited, caused by dividing across the wrong population (wrong denominator). The same bug class — an accounting mismatch where the amount deducted does not match the amount credited — exists in `SignableTransaction::new`: the transaction's outputs include an OP_RETURN data output, but the weight/vbyte calculation (the "denominator" of the fee) only accounts for `payments`, silently producing a transaction whose real fee is lower than both the requested feerate and the value reported by `needed_fee()`.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` pushes the OP_RETURN output into `tx_outs` (lines 194-202) *before* computing the transaction weight, but then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204), which builds a mock transaction containing only `payments` — the data output is never included.

The same omission occurs in the change path (lines 224-235): `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` again ignores the data output, and `change_value = input_sat - payment_sat - fee_with_change` is computed with the underestimated `fee_with_change`.

Consequences:
1. `needed_fee` and the change output are computed against a fee that undercounts the OP_RETURN output's ~10-90+ vbytes.
2. The minimum-relay-fee check at line 211 (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) uses the underestimated `vbytes`, so a transaction whose *actual* fee rate is below the relay minimum can pass validation.
3. The actual fee paid (`sum(inputs) - sum(outputs)`, per `fee()` at line 139) equals the underestimated `needed_fee` — the payer "pays" `fee_per_vbyte * vbytes` as the fee while the fee the network actually requires for that feerate is higher. The difference is silently absorbed into the change output, i.e., the change receiver is credited funds that were supposed to pay the fee.

An unprivileged caller reaches this purely through the public inputs `payments`, `change`, and `data` to `SignableTransaction::new` — no validator or collusion required.

### Impact Explanation
Any transaction created with `data: Some(_)`:
- pays a real feerate lower than `fee_per_vbyte` requested (and lower than `needed_fee()` claims),
- can fall below `DEFAULT_MIN_RELAY_TX_FEE` on its actual size, making it non-relayable/non-standard — the spent inputs remain locked until a corrected transaction is signed, so funds are received by the change output that are not spendable as intended,
- mis-credits the change output by `fee_per_vbyte * vbytes_of(data_output)`, corrupting fee accounting between payer (fee) and receiver (change).

This mirrors the report: an amount debited on one side does not equal the amount credited on the other because the distribution base (here, transaction weight) omitted a component.

### Likelihood Explanation
`SignableTransaction::new` is a public API and `data` is an ordinary caller-controlled argument. The bug triggers deterministically whenever `data.is_some()` — no edge-case ordering, races, or malicious counterparties are needed. The severity is bounded because the in-repo caller (`processor/src/networks/bitcoin.rs::make_signable_transaction`) currently passes `None` for `data`, so manifesting impact requires an integrator that uses the OP_RETURN path; when used, the failure is deterministic.

### Recommendation
Compute weight and change against the actual output set. Pass `tx_outs` (or `payments` plus the serialized data output) into `calculate_weight_vbytes`, e.g.:

```rust
// build data_script once, then:
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), &tx_outs_scripts, None);
```

where `tx_outs_scripts` includes `ScriptBuf::new_op_return(...)`. Equivalently, change `calculate_weight_vbytes` to accept `&[TxOut]` and feed it the real `tx_outs`, then recompute `needed_fee`, the min-relay check, and the change subtraction against that size.

### Proof of Concept
```rust
// networks/bitcoin SignableTransaction::new with:
let payments = vec![(p2tr_script_buf(key).unwrap(), 10_000)];
let data = Some(vec![0u8; 80]); // max allowed
let tx = SignableTransaction::new(inputs, &payments, Some(change_addr), data, fee_per_vbyte).unwrap();

// tx.needed_fee() == fee_per_vbyte * vbytes(payments + change)
// but tx.transaction() additionally contains the OP_RETURN output,
// so the real vsize is larger and the real feerate is:
//   tx.fee() / real_vsize  <  fee_per_vbyte
// With fee_per_vbyte * omitted_vbytes >= DUST, the change output is
// over-credited and the TX can fall below the min relay feerate.
```
The root cause is visible directly at `send.rs:194-204` (data pushed to `tx_outs`, weight computed from `payments` only) and `send.rs:224-232` (change computed with the same omitted output).