### Title
OP_RETURN data output excluded from fee calculation causes systematic fee underpayment - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` pushes the `OP_RETURN` data output into `tx_outs` *before* computing the transaction weight, but `calculate_weight_vbytes` rebuilds a fresh transaction only from `payments` and `change` — it never includes the `OP_RETURN` output. The declared `needed_fee` therefore under-pays the real transaction's fee rate whenever `data` is specified, and the actual fee can silently fall below the minimum relay fee.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

1. The OP_RETURN output is appended to `tx_outs` at lines 194-202.
2. Weight/vbytes are then computed at lines 204 and 225-226 via `Self::calculate_weight_vbytes(tx_ins.len(), payments, change)`, which reconstructs a transaction containing only `payments` and an optional change output — the `data` output is never passed in [1](#0-0) .
3. `needed_fee = fee_per_vbyte * vbytes` (line 206) is computed on this smaller transaction, while the final `SignableTransaction.tx` includes the extra OP_RETURN output [2](#0-1) .
4. When a change address is present, change is computed as `input_sat - payment_sat - fee_with_change` (line 228), so the change amount itself is arithmetically correct, but the *effective* fee rate of the broadcast transaction is `fee / (vbytes + ~92)` instead of `fee_per_vbyte`.

This mirrors the reported bug class: a real component of the transaction (the data output's weight) exists but is invisible to the accounting (the fee computation), so the declared accounting does not match what is actually signed and broadcast.

### Impact Explanation
- `needed_fee()` (lines 133-135) returns a fee that does not achieve the caller-requested `fee_per_vbyte`. With an 80-byte OP_RETURN (~92 serialized bytes, all non-witness), the missing weight is significant relative to a minimal 1-in/2-out transaction (~140 vbytes), so the real fee rate can drop by roughly a third.
- The minimum-fee check at line 211 is evaluated against the under-estimated vbytes, so a transaction whose *actual* fee rate is below `DEFAULT_MIN_RELAY_TX_FEE` can be constructed and signed. Such a transaction will be rejected by standard relay/mining policy: the signed inputs' value is committed (inputs - outputs is fixed by the sighash), yet the transaction cannot confirm — funds are spent but not spendable until a higher-fee replacement is signed.
- Callers selecting a fee rate to meet a confirmation target (e.g., the processor's median-fee path in `processor/src/networks/bitcoin.rs`) would systematically underpay whenever `data` is used.

### Likelihood Explanation
Deterministic: any call to `SignableTransaction::new` with `Some(data)` produces an under-weight fee. It requires only the public `data` parameter (≤ 80 bytes, line 171), no privileged position. The sole mitigating factor is that the in-tree processor passes `None` for `data` [3](#0-2) , so exploitability depends on integrator use of the data field — but the library contract ("the fee necessary for this transaction to achieve the fee rate specified", line 129-131) is violated for all such callers.

### Recommendation
Include the data output in the weight estimation. Pass the fully-constructed `tx_outs` (or an `Option<&ScriptBuf>` for the OP_RETURN script plus a zero amount) into `calculate_weight_vbytes`, or compute the weight directly on the final `Transaction`. Re-run the `TooLowFee` check against the real vbytes, and document that `needed_fee` accounts for all outputs.

### Proof of Concept
```rust
// In SignableTransaction::new (send.rs):
//   tx_outs.push(OP_RETURN)            // line 195
//   let (_, vbytes) = calculate_weight_vbytes(inputs, payments, None);
//                                      // line 204 -- data output absent
//   needed_fee = fee_per_vbyte * vbytes;
//
// Construct: 1 input, 1 payment, Some(change), Some(vec![0; 80])
// Real tx includes the OP_RETURN (~92 extra non-witness bytes).
// needed_fee only covers the no-data size.
//
// With fee_per_vbyte = 1 (>= min relay check on under-estimated size):
//   actual_vbytes ≈ vbytes_estimated + 92
//   actual_feerate = needed_fee / actual_vbytes < 1 sat/vB
//   -> below DEFAULT_MIN_RELAY_TX_FEE, non-relayable yet validly signed.
//
// Verification: compare tx.vsize() of the returned SignableTransaction
// against needed_fee / fee_per_vbyte — they differ whenever data.is_some().
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
