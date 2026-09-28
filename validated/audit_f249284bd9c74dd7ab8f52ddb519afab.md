### Title
`SignableTransaction::new` computes the fee and weight bound without the OP_RETURN output, underpaying the requested fee rate and bypassing the standard-weight check - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes an OP_RETURN output into `tx_outs` (send.rs:194-202) but then calls `calculate_weight_vbytes(tx_ins.len(), payments, None)` (send.rs:204) and `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (send.rs:225-226), neither of which has any parameter for the data output. The constructed weight-estimation transaction inside `calculate_weight_vbytes` (send.rs:68-99) only includes `payments` and optionally `change`. This is the same bug class as the report: an accounting formula that silently omits a term present in the real value being accounted for, reachable entirely from public inputs (`data`, up to 80 bytes).

### Finding Description
`SignableTransaction::new` accepts `data: Option<Vec<u8>>` and, after the ≤80-byte check at send.rs:171-173, appends an OP_RETURN `TxOut` to `tx_outs` at send.rs:194-202. It then computes `(mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at send.rs:204. Inside `calculate_weight_vbytes` the estimation transaction's `output` vector is built solely from `payments` plus the optional `change` output (send.rs:85-99); the OP_RETURN output is never represented. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) is computed for a transaction missing the OP_RETURN output (~4 bytes of `compactsize`/value overhead + ~1-2 bytes script length + up to ~83 script bytes ≈ 90+ real bytes, plus a 4-byte-per-output weight delta). The actual broadcast transaction is larger, so the effective fee rate is strictly below the caller-requested `fee_per_vbyte`.
2. The change-amount/fee branch at send.rs:224-234 repeats the same omission: `fee_with_change` is computed on a transaction without the OP_RETURN output, and `value = input_sat - (payment_sat + fee_with_change)` over-credits the change output by exactly the missing fee component, so the paid fee ends up below the target rate rather than the change absorbing it correctly.
3. The standardness guard `weight > MAX_STANDARD_TX_WEIGHT` at send.rs:241 is evaluated against the underestimated `weight` variable, so a transaction pushed over the standard weight limit only by its OP_RETURN output passes the check and produces a signed transaction Bitcoin nodes will reject as non-standard.

### Impact Explanation
Any caller supplying `data` gets a threshold-signed transaction whose fee rate is below the requested `fee_per_vbyte` — degrading to unconfirmable or slow-confirming — or, at the boundary, a fully signed transaction exceeding `MAX_STANDARD_TX_WEIGHT` that cannot be relayed at all. This is an incorrect accounting/verifier formula over publicly supplied bytes: the `data` field is attacker-controlled transaction data fed into `SignableTransaction::new`, and the error requires no key access, no malicious peer, and no collusion — only an honest signing set executing the documented API.

### Likelihood Explanation
Deterministic: every call to `SignableTransaction::new` with `data: Some(_)` miscounts. OP_RETURN payloads are a routine input for this wallet (e.g., 80-byte encoded instructions), so the path is exercised whenever a spend attaches data. The concrete harm (fee below market → stuck; weight check bypass → permanently unbroadcastable signed output set) occurs whenever the shortfall matters relative to mempool conditions or the tx sits near the weight limit.

### Recommendation
Include the OP_RETURN output in the size estimation: extend `calculate_weight_vbytes` to take the full output list (or the data length) so both `vbytes`/`needed_fee` and the `fee_with_change`/`weight` recomputation account for the OP_RETURN `TxOut` — i.e., the accounting formula must be `weight(inputs, payments, data, change)`, not `weight(inputs, payments, change)` — and enforce the dust/standardness checks against that complete transaction shape.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// data of 80 bytes passes the check at L171-173 and is pushed at L194-202.
let data = vec![0u8; 80];
let tx = SignableTransaction::new(inputs, &payments, change, Some(data), fee_per_vbyte).unwrap();
// tx.needed_fee() == fee_per_vbyte * vbytes(no-op-return-estimate)
// while tx.transaction().output contains the extra ~95-byte OP_RETURN TxOut,
// so tx.fee() / tx.transaction().vsize() < fee_per_vbyte.
// With inputs/payments sized so real weight is just over MAX_STANDARD_TX_WEIGHT
// only due to the OP_RETURN, the L241 check still passes, yielding a
// signed transaction the network rejects as non-standard.
``` [1](#0-0) [2](#0-1) [3](#0-2) 

Note: scope was limited to the crypto crates and `networks/bitcoin/src`; I verified the FROST signing, BIP-340 negation, Schnorr aggregate weights, and BatchVerifier paths do not exhibit a comparable missing-term flaw, and this is the strongest concrete analog found.

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
