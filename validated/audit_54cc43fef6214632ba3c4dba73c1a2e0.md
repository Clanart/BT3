### Title
`SignableTransaction::new` computes weight/fee over `payments` only, omitting the OP_RETURN data output — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the reported `_fillOrders` bug — where a fee-relevant determination was made using the wrong parameter (`exOrderId` instead of `pairId`) — `SignableTransaction::new` determines the transaction's required fee and change amount by calling `calculate_weight_vbytes` with `payments` (and an optional `change` script) as the only outputs, while the actually-constructed `tx_outs` already includes the OP_RETURN data output. The fee is therefore computed for the wrong output set: the data output's weight is never accounted for.

### Finding Description
In `SignableTransaction::new`, the output list `tx_outs` is built from `payments` and then, when `data` is provided, an OP_RETURN output is pushed at lines 194–202. Only afterwards is the fee calculated, at line 204:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` (lines 62–127) reconstructs a template transaction whose `output` vector is built strictly from `payments` plus an optional `change` output — the OP_RETURN output is never represented in the weight template. The same omission occurs in the change path at lines 225–226, which again passes `payments` (not the real output list) and `Some(&change)`. Consequently:

- `needed_fee = fee_per_vbyte * vbytes` omits roughly `4 * (data_len + ~9)` weight units (~`data_len + 11` vbytes) that the final serialized transaction actually carries.
- The change amount `input_sat - payment_sat - fee_with_change` is likewise computed against an underestimated fee, so change is credited with sats that should have covered the data output's weight.

The effective fee rate of the broadcast transaction is `needed_fee / actual_vsize`, which is strictly below the caller-specified `fee_per_vbyte` whenever `data` is non-empty — up to 80 bytes of under-accounted data (~320 weight units, i.e., ~80 vbytes).

### Impact Explanation
The transaction produced by `TransactionSignMachine`/`complete` pays a lower fee rate than the rate the integrator requested and validated against (`TooLowFee` check at line 211 is also evaluated against the underestimated `vbytes`). In a congested mempool, or when the requested rate was chosen to satisfy a confirmation deadline (e.g., Serai protocol-driven spends carrying in-instruction data), the transaction can linger or be evicted, stalling funds that the protocol believes are moving. The miscalculation is systematic and silent: the transaction is well-formed and will still be signed and accepted where the absolute fee clears relay minimums.

### Likelihood Explanation
Any `SignableTransaction::new` call supplying `Some(data)` triggers the underpayment; the underpayment scales linearly with `data.len()` up to the 80-byte cap. It requires no adversarial capability beyond influencing the `data` field (untrusted transaction metadata fed into the constructor), and it reproduces deterministically.

### Recommendation
Pass the already-built `tx_outs` (or the data length) into the weight/fee calculation so the template transaction matches the real output set. Concretely, change `calculate_weight_vbytes` to accept the full `Vec<TxOut>` being constructed — including the OP_RETURN output — and invoke it with `tx_outs.clone()` for both the no-change and with-change variants, rather than re-deriving outputs from `payments`.

### Proof of Concept
```rust
// inputs: one ReceivedOutput, payments: one payment >= DUST
let data = vec![0u8; 80];
let tx = SignableTransaction::new(vec![output], &payments, None, Some(data.clone()), fee_rate).unwrap();
// tx.needed_fee() == fee_rate * vbytes(inputs + payments only)
// tx.transaction().vsize() == that vsize + (80 + ~11) bytes for the OP_RETURN output
// Therefore tx.fee() / tx.transaction().vsize() < fee_rate
assert!(tx.fee() < fee_rate * u64::try_from(tx.transaction().vsize()).unwrap());
```

Relevant code: the OP_RETURN push at `send.rs:194-202` precedes the weight calculation at `send.rs:204` and the change calculation at `send.rs:224-233`, neither of which models the data output. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-99)
```rust
      output: payments
        .iter()
        // The payment is a fixed size so we don't have to use it here
        // The script pub key is not of a fixed size and does have to be used here
        .map(|payment| TxOut {
          value: Amount::from_sat(payment.1),
          script_pubkey: payment.0.clone(),
        })
        .collect(),
    };
    if let Some(change) = change {
      // Use a 0 value since we're currently unsure what the change amount will be, and since
      // the value is fixed size (so any value could be used here)
      tx.output.push(TxOut { value: Amount::ZERO, script_pubkey: change.clone() });
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L194-206)
```rust
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-233)
```rust
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
```
