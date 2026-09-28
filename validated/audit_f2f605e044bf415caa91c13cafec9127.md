### Title
`SignableTransaction::new` omits the OP_RETURN data output from the fee/weight calculation, producing transactions with a lower effective feerate than requested that may fall below the minimum relay fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
When a `data` payload is supplied to `SignableTransaction::new`, an OP_RETURN output is appended to `tx_outs`, but `calculate_weight_vbytes` is invoked only with `payments` (never including the OP_RETURN output). The computed `needed_fee`, the change-output value, and the `MAX_STANDARD_TX_WEIGHT` check are therefore all derived from a transaction that is smaller than the one actually signed and broadcast. Analogous to the reported class (insufficient handling of attacker-influenced auxiliary data leading to unintended behavior), here attacker-supplied `data` bytes silently alter the signed transaction's effective fee rate.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` builds `tx_outs` from `payments`, then appends an OP_RETURN output when `data` is present (lines 194–202). However, both weight/vbyte estimates exclude it:

```rust
// networks/bitcoin/src/wallet/send.rs
204: let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
...
225: let (weight_with_change, vbytes_with_change) =
226:   Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
```

`calculate_weight_vbytes` (lines 62–127) constructs the measurement transaction solely from `payments` and the optional change script — the OP_RETURN output (~9 + `data.len()` bytes) is never measured. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` underestimates the fee required to hit the requested feerate for the real transaction.
2. The change output is set to `input_sat - payment_sat - fee_with_change`, so the total paid fee stays `fee_with_change` while the serialized transaction is larger — the effective feerate is strictly below `fee_per_vbyte`.
3. The check at line 211 only guarantees `needed_fee >= DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` against the underestimated `vbytes`. A caller targeting the minimum relay feerate will produce a transaction whose actual feerate is below the relay minimum, making it unrelayable and the signed inputs unspendable via that transaction.
4. The `weight > MAX_STANDARD_TX_WEIGHT` check at line 241 uses the underestimated `weight`, permitting construction of a transaction that exceeds the standardness weight limit.

The `data` field is attacker-influenced in the Serai flow (it carries InInstruction/Shorthand payloads destined for burns/outputs), so a party who can cause a large `data` payload to be included degrades the transaction's feerate below what the coordinator intended.

### Impact Explanation
A signed transaction committing the multisig's inputs can be produced with an effective feerate below `bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE` (or above the standard weight cap). Such a transaction will not relay/confirm, locking the referenced inputs until a replacement transaction is constructed and re-signed — funds are received/committed into a spend attempt that is not actually spendable on the network as produced. This is a DoS against the multisig wallet's ability to move funds and violates the caller's explicit `fee_per_vbyte` contract.

### Likelihood Explanation
Any spending path that includes a non-empty `data` payload triggers the underestimate; the amount of underestimate scales with the payload (up to 80 bytes, i.e. ~90+ serialized bytes ≈ up to ~90 vbytes of unpriced weight). With low `fee_per_vbyte` values near the relay minimum, the resulting transaction is non-relayable; with higher rates it merely pays a lower feerate than requested (confirmation delay). Exploitation only requires influencing the `data` field of a transaction the multisig signs, which is within the threat model of parties submitting burn/transfer instructions.

### Recommendation
Include the OP_RETURN output in the fee/weight measurement. Either pass the fully-constructed `tx_outs` (or `payments` plus an explicit data-output entry) into `calculate_weight_vbytes`, or add the serialized size of the OP_RETURN output to the computed weight before deriving `needed_fee`. Apply the same correction in both the no-change and change branches (lines 204–235) so `weight`, `vbytes`, `needed_fee`, `fee_with_change`, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the final transaction.

### Proof of Concept
```rust
// Conceptual: within networks/bitcoin
let data = vec![0xaa; 80]; // max allowed payload
let tx = SignableTransaction::new(
    inputs,                                   // ReceivedOutputs summing to input_sat
    &payments,
    Some(change_script),
    Some(data),
    fee_per_vbyte,                            // e.g. minimum relay rate equivalent
).unwrap();

// needed_fee was computed on a tx WITHOUT the OP_RETURN output.
// The real tx carries ~8 (value) + ~1 (len) + ~3 (script) + 80 bytes extra.
let actual_vsize = tx.transaction().vsize() as u64; // larger than measured `vbytes`
// actual_feerate = tx.fee() / actual_vsize < fee_per_vbyte
// If fee_per_vbyte was chosen at the relay minimum, tx.fee() / actual_vsize
// falls below DEFAULT_MIN_RELAY_TX_FEE and the node rejects the broadcast,
// leaving the consumed inputs unspendable via this signed transaction.
assert!(tx.fee() < fee_per_vbyte * actual_vsize); // feerate lower than requested
```

Relevant code: `SignableTransaction::new`/`calculate_weight_vbytes` in `networks/bitcoin/src/wallet/send.rs` (lines 62–256), and the sighash/`Prevouts::All` commitment to the full output set in `TransactionSignMachine::sign` (lines 373–397), which binds the produced signature to the oversized transaction. [1](#0-0) [2](#0-1)

### Citations

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
