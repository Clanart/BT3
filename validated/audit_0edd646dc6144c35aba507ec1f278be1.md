### Title
OP_RETURN `data` output excluded from fee/weight accounting, over-crediting change and underpaying the real fee - (networks/bitcoin/src/wallet/send.rs)

### Summary
The Burve bug class is: an externally-charged fee is deducted from the tokens actually received, while internal accounting credits the user the full pre-fee amount — the credited balance exceeds the real backing and later withdrawals are under-collateralized. The same shape exists in `SignableTransaction::new`: the caller may attach an OP_RETURN `data` output, but both calls to `calculate_weight_vbytes` are made with `payments` only, so the data output's weight is never priced into `needed_fee`/`fee_with_change`. The change output is then funded as if that fee had been charged (`change = inputs - payments - fee_with_change`), i.e. value that should have gone to the fee is credited to change — and the transaction's real fee rate falls below the requested `fee_per_vbyte`, potentially below the minimum relay fee. [1](#0-0) 

### Finding Description
`SignableTransaction::new` appends the OP_RETURN output to `tx_outs` (lines 194-202), yet the vbytes used for `needed_fee` (line 204) and for the change-aware fee `fee_with_change` (line 226) are computed from `payments` alone — `calculate_weight_vbytes` has no parameter for the data output. An OP_RETURN output carrying up to 80 bytes adds roughly 90+ vbytes that are never priced. Consequences:

- When a `change` script is provided, the change `TxOut` gets `input_sat - payment_sat - fee_with_change`, where `fee_with_change` is too small. The actual fee paid (`inputs - outputs`) is therefore less than `needed_fee`, contradicting the documented contract of `needed_fee()` (lines 129-135) — analogous to crediting Alice 100 value when only 99 assets reached the vault.
- The `TooLowFee` check (lines 211-213) compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes`, both computed with the underestimated vbytes, so a transaction whose true fee rate is below the relay minimum can pass the check. Such a transaction will not propagate through standard mempool policy; its inputs are committed (the FROST signatures are bound to the sighash), so the outputs are effectively unspendable until a new transaction is constructed and re-signed.

### Impact Explanation
Medium. For any caller that uses `data` (the API explicitly supports it), the transaction either (a) underpays the fee relative to the agreed rate, leaking extra funds into the change output — a real accounting discrepancy between "fee the plan committed" and "fee actually paid" — or (b) falls below the min relay fee and is unbroadcastable, stalling the multisig spend that consumed the inputs. This is the same loss-of-backing pattern as H-1, shifted from vault share fees to per-vbyte miner fees.

### Likelihood Explanation
The defect triggers deterministically whenever `data` is `Some` — every such transaction misprices its fee. Whether it crosses into unrelayability depends on how close the requested `fee_per_vbyte` is to the relay minimum and the size of the data. In-repo callers pass `None`, so protocol-internal sends are unaffected today, but the public wallet API accepts arbitrary caller data.

### Recommendation
Pass the data output (or a synthesized `TxOut` for it) into `calculate_weight_vbytes` for both the no-change and with-change calculations, so `needed_fee` and `fee_with_change` reflect the transaction actually broadcast. Alternatively, build the weight template directly from the final `tx_outs` vector plus the optional change output.

### Proof of Concept
```rust
// networks/bitcoin/tests/wallet.rs shape
let payments = [(p2tr_script_buf(key).unwrap(), 10_000)];
let data = vec![0u8; 80]; // max allowed OP_RETURN payload

let tx = SignableTransaction::new(
  vec![output],          // input with ample value
  &payments,
  Some(change_addr),     // change requested
  Some(data),
  FEE,
).unwrap();

// The serialized transaction is larger than the vbytes used for needed_fee
let actual_vbytes = tx.tx.vsize() as u64; // includes the ~90+ vbyte OP_RETURN output
assert!(tx.needed_fee() < actual_vbytes * FEE);      // underpriced
assert!(tx.fee() < tx.needed_fee());                 // violates documented contract
// With FEE at the relay minimum, the produced TX has fee rate < 1 sat/vbyte
// and will be rejected by standard mempool policy despite passing TooLowFee check
```

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
