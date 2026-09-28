### Title
Fee and weight calculated without the OP_RETURN `data` output in `SignableTransaction::new`, producing under-priced or oversized transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

The analog of the Tokensoft finding — a missing required fee component rendering an operation inoperative — exists in Serai's Bitcoin wallet. `SignableTransaction::new` accepts an optional `data` payload which is appended as an `OP_RETURN` output, but the transaction weight/vsize used to compute `needed_fee` (and the `MAX_STANDARD_TX_WEIGHT` check) is derived from `calculate_weight_vbytes`, which builds a model transaction containing only `payments` and optionally `change`. The `data` output is never included in that model, so its bytes are not paid for and not counted against the standardness weight limit.

### Finding Description

In `SignableTransaction::new`:

1. The `OP_RETURN` output is pushed onto `tx_outs` first: [1](#0-0) 
2. The weight/vbytes used for fee computation are then computed *only* over `tx_ins.len()` and `payments`, with no `data` parameter at all: [2](#0-1) 
3. `calculate_weight_vbytes` builds the measurement `Transaction` solely from `payments` and the optional `change` script — it has no way to account for the data output: [3](#0-2) 
4. The minimum-relay-fee check, the `NotEnoughFunds` check, and the `MAX_STANDARD_TX_WEIGHT` check all consume this understated `vbytes`/`weight`: [4](#0-3) 
5. The final transaction is built from `tx_outs`, which *does* include the (up to 80-byte) `OP_RETURN` output: [5](#0-4) 

Concretely, `needed_fee = fee_per_vbyte * vbytes` is correct for a transaction without the data output, but the signed transaction is larger by roughly `8 + 1 + script_len` (≈ 91+ weight units for an 80-byte payload) vbytes/weight-units. The actual fee paid is `sum(inputs) − sum(outputs)`, which equals `needed_fee` when there is no change, so the realized fee rate is strictly below `fee_per_vbyte`. Because the `TooLowFee` guard checks `needed_fee` against the *understated* vbytes, a transaction whose true fee rate falls below `DEFAULT_MIN_RELAY_TX_FEE` (1 sat/vbyte) can still pass construction — mirroring the reference bug where a missing `relayerFee` makes the crosschain claim silently inoperative: the produced transaction cannot be relayed by default-policy nodes. Additionally, a transaction with many inputs/payments plus a data output can exceed `MAX_STANDARD_TX_WEIGHT` undetected, since the checked `weight` excludes the data output.

### Impact Explanation

- The caller receives an `Ok(SignableTransaction)` for a transaction whose true fee rate is lower than requested and potentially below the network minimum relay fee, so it will not propagate or confirm — the operation is inoperative despite appearing valid, the same failure shape as `xcall` without `relayerFee`.
- Funds are effectively locked: the inputs are consumed by an un-relayable transaction and the plan must be re-created/re-signed.
- The `MAX_STANDARD_TX_WEIGHT` sanity check is bypassable by up to the size of the data output, producing non-standard transactions that `send_raw_transaction` will reject.
- If change is present, `needed_fee` is recomputed with change included (`fee_with_change`) but still without the data output, so the change amount is correct but the fee remains underpaid.

### Likelihood Explanation

Any caller passing `data` to `SignableTransaction::new` (the documented "If data is specified, an OP_RETURN output will be added with it" feature) hits this on every such transaction — deterministic, not dependent on attacker behavior beyond supplying a payload-bearing request. The larger the `data` (up to the 80-byte cap) and the tighter the fee budget, the more likely the result falls below relay minimums.

### Recommendation

Pass the data output into the weight model: extend `calculate_weight_vbytes` to accept `data: Option<&[u8]>` (or take the already-built `tx_outs`) and include the `OP_RETURN` `TxOut` in the model transaction before computing `weight`/`vbytes`, so `needed_fee`, the `TooLowFee` check, the change-value computation, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the transaction that is actually signed.

### Proof of Concept

```rust
// networks/bitcoin: conceptual reproduction
let data = vec![0u8; 80];
let tx = SignableTransaction::new(inputs, &payments, None, Some(data.clone()), FEE).unwrap();
// tx.needed_fee() == FEE * vbytes(tx without OP_RETURN)
// actual signed tx contains the ~84-byte OP_RETURN output, so
// tx.fee() / tx.vsize() < FEE; for FEE near the 1 sat/vbyte floor the real
// fee rate drops below DEFAULT_MIN_RELAY_TX_FEE and the TX is un-relayable.
```

The unit test `test_transaction_errors` constructs transactions with `Some(vec![0; 80])` but only asserts `is_ok()`, never that `tx.fee() >= FEE * actual_vsize`, so the underpayment is unobserved [6](#0-5) .

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L62-99)
```rust
  fn calculate_weight_vbytes(
    inputs: usize,
    payments: &[(ScriptBuf, u64)],
    change: Option<&ScriptBuf>,
  ) -> (u64, u64) {
    // Expand this a full transaction in order to use the bitcoin library's weight function
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
        inputs
      ],
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

**File:** networks/bitcoin/src/wallet/send.rs (L194-202)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-206)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L211-243)
```rust
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L245-251)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
```

**File:** networks/bitcoin/tests/wallet.rs (L185-191)
```rust
    assert!(
      SignableTransaction::new(inputs.clone(), &payments, None, Some(vec![0; 80]), FEE).is_ok()
    );
    assert_eq!(
      SignableTransaction::new(inputs.clone(), &payments, None, Some(vec![0; 81]), FEE),
      Err(TransactionError::TooMuchData),
    );
```
