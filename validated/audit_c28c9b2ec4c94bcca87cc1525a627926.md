### Title
OP_RETURN data output omitted from weight/vsize accounting causes understated fee and overstated change - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes the `OP_RETURN` data output into `tx_outs` before computing the transaction's weight and virtual size, but `calculate_weight_vbytes` is called only with `payments` and the optional `change` script. The `OP_RETURN` output is therefore excluded from `vbytes`, `needed_fee`, the `MAX_STANDARD_TX_WEIGHT` check, and the change calculation. This is the same bug class as the reference finding: an accounting calculation (`balanceOf`) that omits an encumbrance (`pendingPayouts`), producing an inflated result. Here, an output is omitted from the fee/size accounting, producing an understated fee and an overstated spendable "change" balance.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs:194-235`:

- The `OP_RETURN` output is appended to `tx_outs` at lines 194-202.
- `(mut weight, vbytes)` is then computed at line 204 as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — `payments` does not include the `OP_RETURN` output, so `vbytes` (and therefore `needed_fee = fee_per_vbyte * vbytes` at line 206) does not reflect the serialized `OP_RETURN` output's ~`11 + len(data)` weight units.
- The `TooLowFee` check at line 211 and the `NotEnoughFunds` check at line 215 both use this understated `needed_fee`.
- The change path at lines 224-235 calls `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`, again omitting the `OP_RETURN`. The change output value is computed as `input_sat - payment_sat - fee_with_change`, so the change is credited the satoshis that should have paid for the `OP_RETURN`'s weight.
- The `MAX_STANDARD_TX_WEIGHT` check at line 241 uses `weight`, which also excludes the `OP_RETURN` output, so a transaction that exceeds the standardness limit can be accepted.
- The public docs on `fee()` (line 137-141) state "the actual fee this transaction will use is `sum(inputs) - sum(outputs)`", which will equal the nominal `needed_fee` while the *effective* fee rate `actual_fee / actual_vsize` is lower than the caller-specified `fee_per_vbyte`, because `actual_vsize > vbytes`.

An `OP_RETURN` output carrying the maximum allowed 80 bytes adds roughly 91 WU (~23 vbytes) that is never priced.

### Impact Explanation
The transaction is constructed with an effective fee rate strictly below the `fee_per_vbyte` requested by the caller, and below the rate validated by the `TooLowFee` check. For an `OP_RETURN` near the 80-byte cap, the effective rate can be materially lower; if `fee_per_vbyte` was chosen near the minimum relay rate, the resulting transaction can fail `DEFAULT_MIN_RELAY_TX_FEE` policy on actual vsize and be rejected by the mempool — the wallet then holds a signed transaction paying its inputs that cannot propagate, and the change output (which was inflated by the unpriced weight) reports funds that are not spendable on-chain. Additionally, a transaction whose true weight exceeds `MAX_STANDARD_TX_WEIGHT` passes the size guard, producing a non-standard, non-relayable transaction.

### Likelihood Explanation
The miscalculation triggers on every `SignableTransaction::new` call where `data` is `Some` and non-empty — no race, no ordering dependency, and no adversarial cryptographic input required. Any Serai flow that attaches an `OP_RETURN` payload (e.g., relaying instruction data) hits this path deterministically. The missing weight is small (~23 vbytes maximum due to the 80-byte `TooMuchData` cap), so in practice the effective feerate usually only dips slightly below the requested rate; it becomes exploitable/fatal only when the caller selects a feerate near the relay minimum or near the standardness boundary, capping severity at Medium.

### Recommendation
Include the `OP_RETURN` output in the size accounting. Either pass the complete output list (payments + data output) into `calculate_weight_vbytes`, or add the `OP_RETURN`'s serialized size (`TxOut` overhead + `OP_RETURN` script of `len(data)` pushbytes) to `weight`/`vbytes` before computing `needed_fee`. The same corrected weight must feed the `TooLowFee`, `NotEnoughFunds`, change, and `MAX_STANDARD_TX_WEIGHT` computations so that `fee()`'s documented invariant (`actual fee rate ≥ fee_per_vbyte`) holds.

### Proof of Concept
```rust
// networks/bitcoin context: given one confirmed ReceivedOutput `input`
// worth 100_000 sats to `key`, and a Scanner-registered change script.
let payments = [(payment_script, 10_000u64)];
let data = vec![0xAA; 80]; // max allowed OP_RETURN payload

let stx = SignableTransaction::new(
  vec![input],
  &payments,
  Some(change_script.clone()),
  Some(data),
  FEE_PER_VBYTE,
).unwrap();

// The signed tx contains payments + OP_RETURN + change = 3 outputs,
// but `needed_fee` was priced for payments + change only.
let real_vsize = stx.transaction().vsize() as u64; // includes OP_RETURN
let actual_fee = stx.fee(); // == stx.needed_fee()
let effective_rate = actual_fee as f64 / real_vsize as f64;

// effective_rate < FEE_PER_VBYTE because ~23 vbytes were never priced.
assert!(effective_rate < FEE_PER_VBYTE as f64);
// change was over-credited by (real_vsize - priced_vbytes) * FEE_PER_VBYTE.
```

Relevant code: [1](#0-0)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-235)
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
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

    // If there's a change address, check if there's change to give it
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
```
