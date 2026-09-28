### Title
OP_RETURN `data` output excluded from fee/weight calculation, causing underpriced and potentially unrelayable signed transactions — (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
Analogous to the Perennial issue where an externally-priced cost (`pyth.getUpdateFee()`) was paid inside a keeper call but never included in the keeper's compensation, `SignableTransaction::new` adds a caller-supplied `data` OP_RETURN output to the transaction *outputs* but omits it from the *weight/vbyte calculation* used to price the fee. The transaction therefore pays `fee_per_vbyte` only for the payment/change outputs, silently underpaying for up to ~90 additional vbytes. In a sufficiently elevated fee market the resulting transaction can fall below the mempool minimum relay fee for its actual size, so a fully signed, valid transaction is produced that the network will refuse to relay or confirm — the Serai analogue of "keepers refusing to work": work is performed and committed, but the unpriced external cost makes the result unexecutable.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` appends the OP_RETURN output to `tx_outs` at lines 194–202:

```rust
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}
``` [1](#0-0) 

However, the weight used to derive `needed_fee` is computed from `payments` only — the `data` output is never passed to `calculate_weight_vbytes`:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
``` [2](#0-1) 

The same omission occurs in the change branch, where `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` again only sees `payments`, not `tx_outs` (which already contains the OP_RETURN) (`networks/bitcoin/src/wallet/send.rs:224-235`). Additionally, the `TooLowFee` check at lines 211–213 validates `needed_fee` against the *underestimated* `vbytes`, so it cannot catch the deficiency:

```rust
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
``` [3](#0-2) 

The actual fee paid is `sum(inputs) - sum(outputs)` (`fee()`, lines 138–141), which equals `needed_fee` — a value priced for a smaller transaction than the one signed. Since `calculate_weight_vbytes` builds the dummy transaction solely from `payments` and an optional `change` (`send.rs:85-99`), the OP_RETURN's ~11–93 vbytes (8-byte amount + script for up to 80 bytes of data, `TooMuchData` cap at line 171) are never charged for.

### Impact Explanation
`SignableTransaction` is the object the threshold multisig signs via `sign`/FROST (`send.rs` imports `frost::sign::*`). Once signed, the transaction's effective sat/vbyte rate is `needed_fee / actual_vsize`, strictly below `fee_per_vbyte`. Two concrete harms:

1. **Transaction below relay minimum / stuck funds**: with an 80-byte payload the actual vsize exceeds the estimated `vbytes` by ~90 vbytes. At `fee_per_vbyte = 1 sat/vb`, `needed_fee` can satisfy `DEFAULT_MIN_RELAY_TX_FEE * estimated_vbytes` while being below `DEFAULT_MIN_RELAY_TX_FEE * actual_vbytes`, so Bitcoin Core rejects it at `sendrawtransaction`. The inputs (FROST-controlled UTXOs) are consumed by an unbroadcastable, already-signed transaction that cannot be re-priced without re-signing.
2. **Fee-market mispricing**: even when relayable, the transaction pays materially less than the rate the caller requested and will confirm late or never, leaving the wallet's accounting (`needed_fee`/`fee`) reporting a fee rate that was never achieved.

This is a "funds/cost not compensated" class bug: the cost of embedding attacker/user-supplied data is pushed onto the signers' inputs without being priced.

### Likelihood Explanation
Any caller of the public `SignableTransaction::new` API supplying `data: Some(..)` triggers it deterministically — no race, no privileged position needed; the bytes in `data` are untrusted public input. Severity is bounded by the 80-byte data cap (~90 vbytes of unpriced weight), keeping it a Medium-impact mispricing/stuck-funds issue rather than a direct theft.

### Recommendation
Include the OP_RETURN output in the weight estimate: pass the populated outputs (or an explicit `data: Option<&[u8]>` parameter) into `calculate_weight_vbytes`, e.g. build the dummy `tx.output` from `tx_outs` plus the hypothetical change output, at `send.rs:204` and `send.rs:225`. Alternatively, validate `fee() >= DEFAULT_MIN_RELAY_TX_FEE * actual_vsize` on the final `Transaction` before returning `Ok`.

### Proof of Concept
```rust
// networks/bitcoin context: 1 input, no payments, only data
let inputs = vec![received_output]; // e.g. 10_000 sats
let data = vec![0u8; 80];
let tx = SignableTransaction::new(inputs, &[], None, Some(data.clone()), 1).unwrap();

// Estimated vbytes used for pricing excludes the ~93-vbyte OP_RETURN output.
// needed_fee = 1 * vbytes_without_opreturn, but actual tx contains it.
// Check effective rate:
let signed = sign(&keys, &tx); // FROST-signed
let actual_vsize = signed.vsize() as u64;
assert!(tx.fee() < actual_vsize); // pays less than 1 sat/vb on the real size
// For a ~200-estimated-vbyte TX, actual ~293 vbytes: fee 200 sats < 293 sats
// -> below DEFAULT_MIN_RELAY_TX_FEE for its size; send_raw_transaction rejects it.
```

The existing test at `networks/bitcoin/tests/wallet.rs:185-187` (`Some(vec![0; 80])` accepted as `is_ok()`) confirms the path is exercised but never asserts `fee() >= fee_per_vbyte * actual_vsize`.

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L210-213)
```rust
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
```
