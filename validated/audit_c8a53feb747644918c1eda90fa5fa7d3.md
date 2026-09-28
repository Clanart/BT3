### Title
`SignableTransaction` underestimates the required fee when an OP_RETURN data output is present, producing transactions paying less than the requested fee rate - (networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes `needed_fee` via `calculate_weight_vbytes`, which builds a mock transaction containing only the inputs and payment outputs. When the caller supplies `data`, an OP_RETURN `TxOut` (up to 80 bytes of payload plus script overhead) is pushed onto `tx_outs` *before* the weight calculation, yet `calculate_weight_vbytes` is invoked with `payments` only — the data output is never included in the estimated weight/vbytes. The analog to the reference report ("staking withdraws DAI from PolicyBook, leaving insufficient funds for claims") is a resource-accounting mismatch: the transaction commits fewer satoshis to fees than its real size requires, so the remaining balance silently funds a below-rate (potentially non-relayable) transaction instead of covering the obligation it was sized for.

### Finding Description
At send.rs:194-202, the OP_RETURN output is appended to `tx_outs`. Then at send.rs:204, `calculate_weight_vbytes(tx_ins.len(), payments, None)` is called with only `payments`, and `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) is derived from that underestimate. The change-path calculation at send.rs:225-227 (`calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`) also omits the data output. The final `SignableTransaction` embeds `tx_outs` including the OP_RETURN (send.rs:245-251), so `fee()` (send.rs:138-141) reflects the real transaction, but `needed_fee` — the amount reserved out of the change — does not.

Concretely, `change = input_sat - payment_sat - fee_with_change` (send.rs:228-230) is computed with `fee_with_change` that excludes the data output's ~90+ weight units. The actual signed transaction therefore pays `fee_per_vbyte` over a smaller vsize than it occupies, so its effective fee rate is strictly below the caller-specified rate. With an 80-byte payload and moderate fee rates this can also push the realized fee below `DEFAULT_MIN_RELAY_TX_FEE` even though the check at send.rs:211 passed, causing the transaction to be rejected by relay policy or to stall in mempools while the threshold group believes funds were spent.

The `data` payload is attacker-influenced: in the Serai processor, OP_RETURN data originates from user-supplied `InInstruction` data embedded in Bitcoin transactions (processor/src/networks/bitcoin.rs `extract_serai_data` / forwarding plans), so an unprivileged depositor can force the multisig to construct spends carrying arbitrary-size (≤80 byte) data payloads and systematically underpay fees.

### Impact Explanation
Any `SignableTransaction` built with `Some(data)` pays less than `fee_per_vbyte` on its true vsize. Effects: (a) the effective fee rate is lower than intended — the discrepancy scales with payload size (up to ~360 WU ≈ 90 vbytes unaccounted); (b) when `needed_fee` barely cleared `TooLowFee`, the realized fee rate can fall under the minimum relay fee, making the transaction unrelayable — multisig funds are then locked in an output that was signed but cannot confirm, i.e., funds reported as spent/sent that are not actually spendable as broadcast; (c) change is slightly overpaid by the unaccounted fee gap going to miners, which is harmless, so the primary impact is fee under-reservation/liveness. This matches the accepted impact class of funds committed to an obligation (fee rate) being insufficient due to an accounting gap.

### Likelihood Explanation
Every transaction carrying data underpays — deterministic, not probabilistic. Whether it crosses into non-relayable territory depends on fee rates and payload size. Since callers specifying `data` include protocol-driven flows embedding user-controlled `InInstruction` data, triggering it requires no privilege. However, the underpayment is bounded (~90 vbytes × fee_per_vbyte), so at typical fee rates the transaction still confirms, merely at a lower effective rate; the relay-failure outcome needs a marginal-fee scenario. Medium likelihood of degraded fee rate; lower likelihood of outright relay failure.

### Recommendation
Include the data output in the weight estimation: either pass a flag/`Option<&ScriptBuf>` for the OP_RETURN output into `calculate_weight_vbytes` (and the change variant), or construct the mock transaction's outputs from the already-built `tx_outs` rather than `payments`, so `needed_fee` reflects the real transaction size. Additionally, re-verify the `TooLowFee` check against the final transaction's actual vsize.

### Proof of Concept
```rust
// Conceptual PoC against networks/bitcoin/src/wallet/send.rs
let inputs = vec![received_output]; // any ReceivedOutput with enough value
let payments = vec![(payment_script, 1000)];
let data = vec![0u8; 80]; // max allowed OP_RETURN payload

let tx = SignableTransaction::new(inputs, &payments, Some(change_script), Some(data), fee_rate).unwrap();

// The signed transaction is ~90 vbytes larger than needed_fee / fee_rate implies.
let real_vsize = tx.transaction().vsize() as u64;
let implied_vsize = tx.needed_fee() / fee_rate;
assert!(real_vsize > implied_vsize); // holds by the OP_RETURN output's size
// Effective fee rate = tx.fee() / real_vsize < fee_rate
```
Root cause: `calculate_weight_vbytes` at send.rs:62-127 builds outputs only from `payments` plus optional change, while the OP_RETURN output added at send.rs:194-202 is never represented in either call site (send.rs:204, send.rs:225-227).