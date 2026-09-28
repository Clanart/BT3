### Title
OP_RETURN data output is excluded from fee/weight calculation, producing underpriced or overweight transactions that cannot confirm - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes the transaction's weight, virtual size, and `needed_fee` via `calculate_weight_vbytes`, which is passed `payments` — a slice that never includes the OP_RETURN output carrying `data` (up to 80 bytes). Any transaction built with `data` therefore underpays its intended fee rate, may fall below the minimum relay fee despite passing the `TooLowFee` check, and can exceed `MAX_STANDARD_TX_WEIGHT` without triggering `TooLargeTransaction`. Analogous to the Atomic Loans incident (borrower-side code flaw making BTC unspendable/unrecoverable), an unprivileged user supplying output data causes the threshold group to sign a transaction that locks funds.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` (lines 194-202), but the fee/weight math uses only `payments`:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```
(networks/bitcoin/src/wallet/send.rs:204-206)

`calculate_weight_vbytes` builds the dummy transaction's `output` list solely from `payments` plus optional `change` (lines 85-99). The same omission occurs in the change path: `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (lines 225-227). A data-bearing output is up to 8 (value) + ~2 (script len) + 2 (OP_RETURN + push opcode) + 80 (payload) ≈ 92 serialized bytes — roughly 92 vbytes, none of which is counted.

Consequences:
1. **Underpaid fee**: `needed_fee = fee_per_vbyte * vbytes` omits the OP_RETURN's contribution, so the actual fee rate is strictly below the requested rate. The change amount is also inflated by `fee_per_vbyte * missing_vbytes` sats, silently transferring value to the change output that was intended as fee — or, without change, simply paying less than intended.
2. **False-negative `TooLowFee` check**: the minimum-relay check at lines 211-213 uses the understated `vbytes`, so a transaction can pass `SignableTransaction::new` yet be rejected by every relaying node (`DEFAULT_MIN_RELAY_TX_FEE` is per actual kilo-vbyte).
3. **False-negative `TooLargeTransaction` check**: the `weight > MAX_STANDARD_TX_WEIGHT` check at line 241 uses the understated `weight`; a transaction near the 400,000 WU standardness limit can exceed it in reality.

### Impact Explanation
The produced `SignableTransaction` is signed by the FROST threshold group via `TransactionSignMachine::sign` (send.rs:355-398) and commits to all prevouts with `Prevouts::All`. Once signed, the transaction either relays at a lower fee rate than intended (deviation from the fee contract the caller specified) or fails to relay/confirm entirely. In the failure cases the selected `ReceivedOutput` inputs are consumed by a transaction that can never confirm, and the outpoints remain "spent" from the signer's perspective (they were handed to a signed, finalized transaction). Recovering the funds requires coordinating an entirely new signing session with a corrected transaction — the exact "BTC locked by a protocol flaw" shape of the Atomic Loans incident, where borrowers' collateral became unspendable under specific (unexploited) circumstances.

### Likelihood Explanation
Deterministic whenever `data: Some(_)` is passed with a low `fee_per_vbyte` or a near-limit transaction. `data` is a caller-supplied `Option<Vec<u8>>` of up to 80 bytes (checked at line 171) — in Serai's usage this carries user-controlled out-instruction/refund metadata, so an unprivileged user crafting a payment with maximal data and a marginal fee rate deterministically produces a non-relayable signed transaction. No malicious validator, key leakage, or collusion is required.

### Recommendation
Include the OP_RETURN output in the weight calculation: either push the data output into the `payments`-equivalent slice before calling `calculate_weight_vbytes`, or add a `data: Option<&[u8]>` parameter to `calculate_weight_vbytes` and append `TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) }` to the dummy transaction's outputs for both the change and no-change calls.

### Proof of Concept
```rust
// networks/bitcoin: construct a 1-input tx with an 80-byte OP_RETURN and a
// fee_per_vbyte exactly at the minimum relay rate.
let data = vec![0xaa; 80];
let tx = SignableTransaction::new(
  vec![received_output],                       // any scanned output
  &[(payment_script, DUST)],                   // one dust-minimum payment
  None,                                        // no change: fee == needed_fee
  Some(data),                                  // 83-byte OP_RETURN output
  1,                                           // 1 sat/vbyte requested
).unwrap();                                    // passes TooLowFee on understated vbytes

// Actual serialized vsize ≈ computed vsize + ~92 bytes.
// Actual fee rate = needed_fee / actual_vsize < 1 sat/vbyte,
// below DEFAULT_MIN_RELAY_TX_FEE => rejected by relaying nodes,
// yet the FROST multisig will still sign and complete it.
```