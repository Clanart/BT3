### Title
`SignableTransaction::new` computes weight/vbytes and fee over an output set that omits the OP_RETURN data output, understating the real transaction size — (`networks/bitcoin/src/wallet/send.rs`)

### Summary
The external report describes the same field (`userBoosts.amount`) being written by two code paths using different scales, letting a user inflate the stored value. In Serai's Bitcoin wallet, the analogous class exists: `SignableTransaction::new` builds the *real* transaction's outputs from `tx_outs` (which includes an attacker-influenced OP_RETURN data output of up to 80 bytes), but computes `weight`, `vbytes`, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` check over a *different* output list — `payments` — that never includes the data output. The fee/size bookkeeping and the actual signed transaction therefore diverge by the size of user-controlled data.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

- The data output is pushed onto `tx_outs` before any size accounting: `tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })` at `send.rs:194-202`.
- The weight/vbytes estimation only serializes `payments`, never the OP_RETURN output: `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at `send.rs:204`, and `calculate_weight_vbytes` builds its template `tx.output` solely via `.map(|payment| ...)` over `payments` at `send.rs:85-93`.
- The same omission occurs in the change path: `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at `send.rs:225-226` also ignores `data`.
- The undercounted `vbytes` feeds `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`), the `TooLowFee` minimum-relay check (`send.rs:211`), the `NotEnoughFunds` check (`send.rs:215`), the change-amount computation (`send.rs:228-230`), and the `MAX_STANDARD_TX_WEIGHT` standardness check (`send.rs:241`) — while `SignableTransaction.tx.output` is built from the larger `tx_outs` (`send.rs:246-251`).

`data` is external input: the constructor accepts `Option<Vec<u8>>` up to 80 bytes (`send.rs:154`, `send.rs:171-172`), and Serai's Out Instructions carry user-specified data to native outputs. A ~89-byte OP_RETURN output adds roughly 356+ weight units (~89+ vbytes) that are never charged for and never counted toward standardness.

### Impact Explanation
- **Underpaid fee**: `fee()` returns `sum(prevouts) - sum(outputs)` (`send.rs:138-141`), which is correct on the real tx, but `needed_fee` was computed for a smaller tx. The signed transaction's effective fee rate is below `fee_per_vbyte`, and can fall under `DEFAULT_MIN_RELAY_TX_FEE` for small requested rates since the `TooLowFee` check also uses the understated `vbytes` — producing transactions the network won't relay/mine, freezing threshold funds in-flight.
- **Standardness bypass**: with many inputs and/or a change output, a transaction that truly exceeds `MAX_STANDARD_TX_WEIGHT` can pass the `send.rs:241` check, yielding a signed transaction that is non-standard and unbroadcastable — funds locked pending manual intervention.
- **Wrong change accounting**: `fee_with_change` at `send.rs:227` is computed without the data output, so the change output absorbs the difference (change is slightly smaller than the fee-rate policy intended).

### Likelihood Explanation
Any party able to attach `data` to a spend (e.g., an Out Instruction carrying data, per Serai's instruction flow) triggers the divergence deterministically; no privileged position is required. The miscalculation is unconditional when `data.is_some()`. Exploitation results in degraded liveness/underpayment rather than direct theft, consistent with a Medium severity.

### Recommendation
Pass the already-constructed `tx_outs` (or an output iterator including the OP_RETURN) into `calculate_weight_vbytes` instead of `payments`, for both the no-change and with-change calls, so that `weight`, `vbytes`, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` check reflect the exact transaction that will be signed. Alternatively, build the full template `Transaction` once — including the data output — and reuse it for both estimation and the final `tx`.

### Proof of Concept
```rust
// networks/bitcoin — conceptual PoC
let data = vec![0u8; 80]; // max allowed by send.rs:171
let tx = SignableTransaction::new(inputs, &payments, None, Some(data), fee_per_vbyte).unwrap();

// Actual signed tx contains the OP_RETURN output (~89 bytes):
assert!(tx.transaction().output.iter().any(|o| o.script_pubkey.is_op_return()));

// But needed_fee was computed only over `payments`:
let actual_vsize = tx.transaction().vsize() as u64;      // includes OP_RETURN
let charged_vsize = tx.needed_fee() / fee_per_vbyte;     // excludes OP_RETURN
assert!(charged_vsize < actual_vsize); // fee underpaid by ~89 * fee_per_vbyte sats
```
The real fee rate `tx.fee() / actual_vsize` is strictly less than the requested `fee_per_vbyte`, and `tx.weight()` may exceed `MAX_STANDARD_TX_WEIGHT` despite passing the check at `send.rs:241`. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** networks/bitcoin/src/wallet/send.rs (L193-213)
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
