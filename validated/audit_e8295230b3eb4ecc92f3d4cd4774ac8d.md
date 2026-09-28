### Title
`SignableTransaction::new` omits the OP_RETURN data output from the weight/vbytes fee calculation, producing transactions that underpay the intended fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends a user-supplied OP_RETURN output to `tx_outs` *before* calling `calculate_weight_vbytes`, but the weight function is fed `payments` — not `tx_outs` — so the data output's bytes (up to ~90 vbytes) are never counted toward `vbytes`, `needed_fee`, or the change-output deduction. The result is a transaction whose actual fee rate is strictly lower than the caller-specified `fee_per_vbyte`, and which can fall below the Bitcoin minimum relay fee despite the `TooLowFee` check passing (the check uses the same underestimated `vbytes`).

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed to `tx_outs` at [1](#0-0) , but the subsequent fee computation calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — passing the `payments` slice, which does not contain the data output [2](#0-1) . `calculate_weight_vbytes` builds its measurement transaction solely from `payments` and the optional `change` script [3](#0-2) . The change path has the identical defect: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` again excludes the OP_RETURN output [4](#0-3) . Consequently `needed_fee = fee_per_vbyte * vbytes` and the `TooLowFee` guard are both evaluated against a transaction that is smaller than the one actually constructed and signed [5](#0-4) . The final `SignableTransaction` includes `tx_outs` (with the data output), so its true vsize exceeds the measured `vbytes` [6](#0-5) .

This mirrors the Compound incident class: a rate/distribution parameter (`needed_fee`, the effective sat/vbyte rate) is computed against incorrect initial conditions, so value is distributed incorrectly at execution — here, change is over-credited and miners are underpaid relative to the requested rate.

### Impact Explanation
Any caller who passes `data` produces a transaction paying less than `fee_per_vbyte`, with the shortfall going to the change output instead of miners. With `fee_per_vbyte` at or near the relay minimum (e.g., 1 sat/vbyte), the true fee rate drops below `DEFAULT_MIN_RELAY_TX_FEE`, so the signed transaction is rejected/non-propagated while the wallet believes it paid a valid fee. Because inputs use `Sequence::MAX`, the transaction is not BIP-125 replaceable, so a stuck transaction cannot be fee-bumped via RBF — the input UTXOs are effectively frozen until the TX confirms or is abandoned [7](#0-6) . Funds are committed to a transaction that is not reliably spendable/confirmable — a reachable, concrete failure rather than a cosmetic miscalculation.

### Likelihood Explanation
`data` is a fully public, unprivileged input to `SignableTransaction::new` (in-scope under `networks/bitcoin/src/wallet`). The miscalculation is deterministic whenever `data.is_some()`: up to 80 bytes of payload plus output overhead (~90 vbytes) is unaccounted. Whether the TX is actually unrelayable depends on the margin between `fee_per_vbyte` and the minimum relay rate, so the reliable-impact case requires a low fee rate — still a normal, reachable configuration.

### Recommendation
Compute the weight/vbytes over the *final* output set. Either push the OP_RETURN output before measurement and pass `tx_outs`-equivalent data to `calculate_weight_vbytes`, or add the serialized size of the data output (`8 + compact_size(len) + script_len`) to the measured weight. Apply the same fix to the change-branch measurement so `fee_with_change` also accounts for the data output.

### Proof of Concept
```rust
// In SignableTransaction::new (networks/bitcoin/src/wallet/send.rs):
let data = vec![0u8; 80];
let tx = SignableTransaction::new(inputs, &payments, Some(change), Some(data), 1).unwrap();

// Measured vbytes exclude the ~90-byte OP_RETURN output:
//   calculate_weight_vbytes(len, payments, None)  // data not in `payments`
// needed_fee = 1 * vbytes  -> passes the TooLowFee check against the same
// underestimated vbytes.
// tx.tx.output includes the OP_RETURN output, so tx.vsize() = vbytes + ~90.
// Actual fee rate = needed_fee / tx.vsize() < 1 sat/vb -> below
// DEFAULT_MIN_RELAY_TX_FEE; non-RBF (Sequence::MAX) => inputs frozen.
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L178-185)
```rust
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L193-202)
```rust
    // Add the OP_RETURN output
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-204)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-213)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
```
