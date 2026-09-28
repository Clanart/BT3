### Title
`SignableTransaction::new` omits the OP_RETURN output from fee/vsize calculation, producing transactions that underpay fees and can fall below minimum relay policy - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
When `SignableTransaction::new` is given a `data` payload, it appends an OP_RETURN output to `tx_outs`, but the virtual-size estimates used for `needed_fee` and the change calculation (`calculate_weight_vbytes`) are built only from `payments` and an optional change output. The OP_RETURN output's weight is never counted, so every transaction carrying Serai's `data` field pays a lower absolute fee and (silently) a lower sat/vbyte rate than requested — potentially below `DEFAULT_MIN_RELAY_TX_FEE`, making the transaction non-relayable and leaving its inputs unspent.

### Finding Description
`SignableTransaction::new` pushes the OP_RETURN output to `tx_outs` before any fee calculation (networks/bitcoin/src/wallet/send.rs:194-202):

```rust
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}
```

However, both calls to `calculate_weight_vbytes` pass only `payments` and the change script:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);   // line 204
...
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));                  // line 226
```

Inside `calculate_weight_vbytes` (lines 85-99), the mock transaction's `output` vector is built exclusively from `payments` plus the optional change output — the OP_RETURN output, which costs `8 (value) + 1 (script len) + 2 (OP_RETURN+push opcodes) + data.len()` serialized bytes (~11–91 bytes, i.e. up to ~91 vbytes since it is entirely non-witness data), is absent.

Consequences:
- `needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change` (line 227) are computed against an underestimated vsize, so the transaction's actual fee rate is `needed_fee / (vbytes + op_return_vbytes)` — strictly less than `fee_per_vbyte`.
- The `TooLowFee` check (line 211) validates the underestimated `needed_fee` against the underestimated `vbytes`, so a `fee_per_vbyte` of 1 sat/vb (which passes the check by equality) yields an actual fee rate below the 1 sat/vb minimum relay rate once the OP_RETURN output is added.
- The change amount (`input_sat - payment_sat - fee_with_change`, line 228) is also computed against the wrong fee, though this only inflates the change by the same marginal amount.

This is the Serai analog of "value sent but unrecoverable/unusable": callers create and sign a transaction believing it meets a given fee rate, while the produced transaction can be unrelayable — funds committed to its inputs are locked until a replacement transaction is coordinated through a fresh threshold signing round.

### Impact Explanation
Any `SignableTransaction` constructed with `data = Some(..)` — which is how Serai embeds `InInstruction` metadata in Bitcoin transactions — underpays its declared fee rate by up to ~90 vbytes worth of fee. At low fee rates (e.g. the minimum relay rate, which the constructor explicitly validates and permits), the resulting transaction is below `DEFAULT_MIN_RELAY_TX_FEE` on a per-vbyte basis and will be rejected by standard Bitcoin nodes. The coordinator then holds a fully signed transaction that cannot be broadcast, and the UTXOs it spends remain locked pending a new signing session with corrected inputs (the same `SignableTransaction` will deterministically produce the same deficient fee). This is a concrete "funds committed but not spendable via the produced artifact" condition reachable purely through public inputs (the `data`, `fee_per_vbyte`, and payment set an unprivileged caller can supply to the transaction-construction API).

### Likelihood Explanation
The bug triggers deterministically whenever `data` is `Some` — there is no probabilistic or adversarial-timing component. Whether it becomes consensus/relay-fatal depends on the chosen `fee_per_vbyte`: at exactly the minimum viable rate (which the code's own `TooLowFee` guard is designed to permit) the transaction is non-standard. At higher rates the tx still relays but pays ~N vbytes fewer sats than intended, degrading confirmation priority and making `needed_fee()`/change accounting inaccurate. Given Serai's Bitcoin flow attaches instruction data to transactions, the affected code path is exercised in normal operation.

### Recommendation
Include the OP_RETURN output in the weight estimation. Either pass the already-built `tx_outs` (or a representation of the data output) into `calculate_weight_vbytes` instead of `payments`, or add the data output explicitly inside `calculate_weight_vbytes` before computing `tx.weight()`:

```rust
// In calculate_weight_vbytes, accept the final tx_outs (payments + OP_RETURN + optional change)
// or append:
if let Some(data) = data {
  tx.output.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(PushBytesBuf::try_from(data).unwrap()),
  });
}
```

Re-run the `TooLowFee` and `NotEnoughFunds` checks against the corrected `vbytes`, and ensure `fee_with_change` is likewise computed over the complete output set.

### Proof of Concept
```rust
// networks/bitcoin context: 1 input of 10_000 sats, an 80-byte data payload,
// a change script, and the minimum fee rate.
let tx = SignableTransaction::new(
  vec![received_output_10k],              // single ReceivedOutput
  &[],                                    // no payments
  Some(change_script),                    // change
  Some(vec![0xAA; 80]),                   // max-size OP_RETURN payload
  1,                                      // 1 sat/vbyte
).unwrap();

// Actual signed tx: 1 P2TR input, change output, OP_RETURN output (~91 bytes)
// tx.weight() reflects all 3 outputs, yet needed_fee was computed for only 2:
let actual_vbytes = tx.transaction().vsize() as u64;
let actual_fee = tx.fee();               // == needed_fee (change absorbs the rest)
// actual_fee / actual_vbytes < 1 sat/vb  -> below DEFAULT_MIN_RELAY_TX_FEE
// -> rejected by `sendrawtransaction` on standard nodes despite passing
//    the constructor's TooLowFee check, which compared against the
//    underestimated vbytes.
```

The underpayment is `fee_per_vbyte * (8 + 1 + 2 + data.len())` sats — up to ~91 sats per vbyte-rate unit — and grows linearly with `data.len()` up to the 80-byte cap enforced at line 171. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** networks/bitcoin/src/wallet/send.rs (L194-213)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
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
```
