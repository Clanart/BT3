### Title
SignableTransaction::new omits the OP_RETURN `data` output from weight/vbytes calculation, undercharging fees and bypassing the standard-weight check - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The original report concerns hardcoding a variable parameter (oracle feed decimals) inside scaling arithmetic, producing systematically wrong results whenever the assumption diverges from reality. The analogous bug in Serai's in-scope code lives in `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`: the transaction size used to compute `needed_fee` and to enforce `MAX_STANDARD_TX_WEIGHT` is calculated from a fixed output set (`payments` plus optional `change`) and hardcodes a 64-byte witness signature, while the caller-supplied `data` OP_RETURN output is appended to `tx_outs` but is never included in either calculation.

### Finding Description
`SignableTransaction::new` appends the attacker/caller-controlled OP_RETURN output to `tx_outs` (`send.rs:193-202`), allowing up to 80 bytes of arbitrary data (`data.as_ref().map_or(0, Vec::len) > 80` check at line 171). However, `calculate_weight_vbytes` is invoked with only `tx_ins.len()`, `payments`, and the change script (`send.rs:204` and `send.rs:225-226`) — the `data` output is never passed in. Consequently:

- `vbytes` excludes the entire serialized OP_RETURN output (8-byte value + compactsize length + up to ~82-byte script, i.e. ~91 bytes, ~91 vbytes of unaccounted size).
- `needed_fee = fee_per_vbyte * vbytes` is therefore under-computed by `fee_per_vbyte * ~91` satoshis relative to the transaction that is actually built and signed.
- The `TooLowFee` check against `DEFAULT_MIN_RELAY_TX_FEE` (line 211) and the `MAX_STANDARD_TX_WEIGHT` check (line 241) are evaluated against the underestimated weight, so a transaction that actually exceeds the standard weight limit, or falls below the minimum relay feerate, can be constructed without error.

This is the same bug class as the Chainlink report: the computation assumes a fixed structure ("all outputs are the payments") instead of accounting for the actual variable output included in the signed object, producing an off-by-a-constant error in value/size arithmetic whenever the assumption is violated (i.e. whenever `data` is `Some`).

### Impact Explanation
An unprivileged caller feeds untrusted `data` bytes into `SignableTransaction::new`, which is exactly the public-input surface the rules allow (transaction data the multisig is caused to sign). The resulting `SignableTransaction` is what the FROST `TransactionMachine`/`TransactionSignMachine` signs via `taproot_key_spend_signature_hash` — the signed transaction commits to the OP_RETURN output while the fee was computed as if it didn't exist.

Concrete consequences:

1. **Underpaid fee**: The real fee `sum(inputs) - sum(outputs)` is fixed, but the *effective feerate* of the broadcast transaction is `fee / actual_vbytes`, lower than the `fee_per_vbyte` the caller/policy specified. Under fee-market pressure the signed transaction can be evicted or never confirmed, stalling funds held by the multisig.
2. **Relay failure**: If `fee_per_vbyte` was chosen to be at/near `DEFAULT_MIN_RELAY_TX_FEE`, the true feerate can fall below the node's minimum relay feerate after accounting for the extra ~91 vbytes, so the transaction the threshold signature authorizes is unrelayable.
3. **Standard-weight bypass**: With `MAX_OUTPUTS`-scale payments plus an OP_RETURN output, the actual weight can exceed `MAX_STANDARD_TX_WEIGHT` (400,000 WU) while the check at line 241 passes, again yielding a transaction nodes reject as non-standard.

In all cases the multisig signs a transaction whose economics differ from what `SignableTransaction::new` validated — funds committed to a transaction that the Bitcoin network will not relay/confirm as constructed.

### Likelihood Explanation
`data` is a caller-controlled parameter (`Option<Vec<u8>>`, up to 80 bytes) and is unconditionally appended to `tx_outs` whenever `Some`. Every call path that supplies `data` — the intended mechanism for attaching Serai `InInstruction`/protocol data to Bitcoin transactions — deterministically triggers the miscalculation; no race or cryptographic assumption is required. The omission is structural: `calculate_weight_vbytes` simply has no parameter for `data`.

### Recommendation
Include the OP_RETURN output in the size calculation. Either pass the serialized `data` length (or a constructed `TxOut` for it) into `calculate_weight_vbytes` and add it to the output list before computing `weight`/`vbytes`, or build the candidate `Transaction` once with all outputs (payments + OP_RETURN + optional change) and derive `weight`, `vbytes`, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` check from that final structure — mirroring the report's recommendation to derive the parameter from the actual value rather than a hardcoded assumption. Optionally also assert `tx.weight()` on the final built transaction before returning.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// In SignableTransaction::new:

// 1. OP_RETURN output is added to tx_outs (lines ~193-202)
if let Some(data) = data {
    tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(...),
    })
}

// 2. But the weight/vbytes calculation only sees `payments` (line ~204):
let (mut weight, vbytes) =
    Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
//    ^ `data` is not an argument; the OP_RETURN TxOut is absent from `tx`
//      inside calculate_weight_vbytes, so `weight`/`vbytes` are too small
//      by ~91 bytes when data = Some(vec![0; 80]).

// 3. needed_fee and the standard-weight check use the underestimated values:
let mut needed_fee = fee_per_vbyte * vbytes;                     // under-charged
if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) { // bypassable
    Err(TransactionError::TooLargeTransaction)?;
}
```

Reproduction: call `SignableTransaction::new(inputs, &payments, None, Some(vec![0u8; 80]), fee_per_vbyte)`. The returned `SignableTransaction::transaction()` contains an OP_RETURN output that contributed nothing to `needed_fee`; `signable.fee() / actual_vbytes < fee_per_vbyte`. With `fee_per_vbyte` set near the minimum relay rate, the resulting signed transaction is below the relay floor and will be rejected by standard Bitcoin nodes.