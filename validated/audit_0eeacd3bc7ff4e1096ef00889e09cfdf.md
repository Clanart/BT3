### Title
Fee/vsize estimation omits the OP_RETURN output, so signed transactions pay a lower feerate than requested and can fall below the minimum relay feerate - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` allows the caller to attach up to 80 bytes of `data`, which is appended as an OP_RETURN output. However, `calculate_weight_vbytes` is only ever called with `payments` (and optionally `change`) — the OP_RETURN output is never included in the weight/vsize estimate. The resulting `needed_fee` is therefore under-computed, and the `TooLowFee` minimum-relay-fee check is evaluated against a smaller vsize than the transaction actually has. The analog to the external report is exact: a feerate cap computed against an underestimated cost basis produces a transaction whose real feerate is lower than intended, causing the cross-system transfer to fail (here: never relay/confirm), permanently stalling the spend of those inputs.

### Finding Description
In `send.rs`, `data` is pushed into `tx_outs` before the weight is computed: [1](#0-0) 

`calculate_weight_vbytes` builds its model transaction purely from `inputs`, `payments`, and `change` — there is no `data` parameter at all: [2](#0-1) 

The minimum-fee check and the funds check both consume this underestimated `vbytes`: [3](#0-2) 

So a caller supplying `data` of up to 80 bytes (checked at line 171) produces a transaction roughly 85+ vbytes larger than what `needed_fee` was priced for, with no correction anywhere. The final `MAX_STANDARD_TX_WEIGHT` check also uses the data-free `weight`.

### Impact Explanation
The actual fee paid is `sum(inputs) - sum(outputs)`, which equals the under-computed `needed_fee`. If the caller requests a feerate at or near the relay minimum (`DEFAULT_MIN_RELAY_TX_FEE`, ~1 sat/vB), the real feerate drops below 1 sat/vB, making the signed transaction non-relayable/non-confirmable. Because `TransactionMachine`/`TransactionSignMachine` produce a FROST signature committing to exactly these inputs and outputs, those UTXOs are consumed by a transaction that can never confirm — the funds are effectively locked, matching the "transfers may fail" impact class of the original report. More generally, any use of `data` silently pays a lower effective feerate than the caller specified.

### Likelihood Explanation
Triggering requires the `data` parameter to be non-`None` and a low requested feerate. The `data` field is public transaction data supplied by the transaction initiator (untrusted input to the signing API), and the underestimate is deterministic — every call with `data` under-charges. In the current `processor` integration `make_signable_transaction` passes `None` for `data` (`processor/src/networks/bitcoin.rs:450`), so the bug is latent in the wallet library rather than exercised on that path; exploitation requires a caller that does attach data while requesting a near-minimal feerate.

### Recommendation
Include the OP_RETURN output in `calculate_weight_vbytes` (add `data`/`Option<&ScriptBuf>` alongside `change`), or compute the fee from the fully-assembled `tx_outs` — including re-checking `TooLowFee` and `MAX_STANDARD_TX_WEIGHT` against the true weight.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs semantics:
// inputs: one ReceivedOutput of value V
// payments: [(script, DUST)]
// change: None
// data: Some(vec![0; 80])
// fee_per_vbyte: 1  (meets relay minimum for the *estimated* vsize)

// calculate_weight_vbytes returns vbytes_est computed WITHOUT the OP_RETURN output.
// needed_fee = 1 * vbytes_est, passing the TooLowFee check.
// The final tx has vbytes_est + ~85 (OP_RETURN output + pushdata overhead),
// so the real feerate is ~ vbytes_est / (vbytes_est + 85) < 1 sat/vB.
// The signed transaction is rejected from relay and can never confirm,
// while its input is committed and cannot be re-spent by the multisig.
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L193-220)
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

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
```
