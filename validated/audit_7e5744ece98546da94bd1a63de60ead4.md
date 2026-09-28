### Title
`SignableTransaction::new` excludes the OP_RETURN data output from weight/vbytes calculation, producing non-relayable transactions and understated fees - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
The external bug class is a mismatch between the amount/size the protocol computes and the amount/size the counterparty actually enforces, leaving residual value mishandled. The analog in `SignableTransaction::new` is that `calculate_weight_vbytes` is called with `payments` only, while the actual transaction's outputs additionally include an OP_RETURN data output of up to ~90 bytes. Consequently `weight`, `vbytes`, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` check are all computed over a smaller transaction than the one actually constructed and signed.

### Finding Description
`SignableTransaction::new` appends the OP_RETURN output to `tx_outs` (send.rs:194-202) but computes size and fee via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at send.rs:204, where `payments` excludes the OP_RETURN output. The same omission occurs in the `Some(&change)` recalculation at send.rs:225-227.

An OP_RETURN output carrying the maximum allowed 80 bytes adds roughly 8 (value) + 1 (script length) + ~3 (opcode/pushdata) + 80 ≈ 92 bytes ≈ 368 weight units (~91 vbytes) that are never counted:

- `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) and `fee_with_change` (send.rs:227) are computed on the undersized vbytes, so the committed fee (especially when a dust-level change output captures only the exact `input_sat - payment_sat - fee_with_change` remainder at send.rs:228-232) yields a *real* feerate below the requested `fee_per_vbyte`, and potentially below `DEFAULT_MIN_RELAY_TX_FEE` — the min-relay check at send.rs:211 also uses the understated `vbytes`.
- The standardness check `weight > MAX_STANDARD_TX_WEIGHT` at send.rs:241 uses the understated `weight`. A transaction counted at, e.g., 399,900 WU passes the check but actually weighs >400,000 WU, violating Bitcoin's standardness policy.

### Impact Explanation
The caller (any user of this library API supplying `data`) gets back a `SignableTransaction` which, once signed via `sign`/`complete` and broadcast, is rejected by relay policy: either its effective feerate is below the minimum relay feerate, or its weight exceeds `MAX_STANDARD_TX_WEIGHT`. The spend the threshold group just signed cannot confirm; the input coins are effectively frozen for that plan until a replacement transaction is constructed and re-signed, and the advertised `needed_fee()`/requested `fee_per_vbyte` is not the feerate achieved. This is a Medium-severity functional/DoS flaw on a public API: no secret leaks, but signed transactions silently violate the bounds the constructor claims to enforce.

### Likelihood Explanation
Reachable whenever `data` is non-`None` and either the base weight is within ~370 WU of the 400,000 WU limit (easily approached near `MAX_INPUTS`/`MAX_OUTPUTS` since each payment and each input adds hundreds of WU), or `fee_per_vbyte` is near the relay minimum. The `data` parameter is a public input to `SignableTransaction::new`.

### Recommendation
Include the OP_RETURN output in the weight/vbytes computation — e.g., append `(ScriptBuf::new_op_return(...), 0)` to the payment list passed to `calculate_weight_vbytes` (the value field is fixed-size so a zero value suffices), or build the full `tx_outs` (including data) before sizing. Apply the same fix to the `change` recalculation at send.rs:225-227.

### Proof of Concept
```rust
// networks/bitcoin (conceptual, library-level)
// One input, no payments, change output, and 80 bytes of OP_RETURN data.
let inputs = vec![received_output]; // any ReceivedOutput
let data = vec![0u8; 80];

let st = SignableTransaction::new(
  inputs,
  &[],                 // no payments
  Some(change_script), // change
  Some(data),
  fee_per_vbyte,       // e.g. exactly the min relay rate
).unwrap();

// st.tx contains the OP_RETURN output, but needed_fee/weight were computed
// as if the tx had ~91 fewer vbytes.
// 1) st.fee() / actual_vbytes < fee_per_vbyte, possibly < min relay rate.
// 2) With ~520 inputs the counted weight can be <= 400_000 while the real
//    weight exceeds it -> transaction fails standardness and is not relayed.
``` [1](#0-0) [2](#0-1)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-211)
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
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-243)
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
      }
    }

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
