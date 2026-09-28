### Title
SignableTransaction fee calculation omits the OP_RETURN output from the transaction weight, underpaying the intended fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the Surge fee-share bug — where the denominator of a share calculation failed to include the newly-added quantity, producing a wrong (over-crediting) result — `SignableTransaction::new` computes the transaction's weight/vbytes, and hence `needed_fee`, over a template transaction that excludes the OP_RETURN data output which is later actually appended to the transaction. The fee formula therefore prices a smaller transaction than the one that is signed and broadcast.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` before the fee is calculated:

```rust
// lines 194-204
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` builds the template transaction's outputs solely from `payments` (lines 85-93) plus an optional change output (lines 95-99). The `data` output — up to 80 bytes plus ~9 bytes of output overhead — is never included. The same omission applies on the change path: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at line 226 still only sums `payments`, so `fee_with_change` also under-accounts when `data` is present.

Two consequences follow, mirroring the "incorrect equation that ignores what was added" structure of the Surge finding:

1. `needed_fee = fee_per_vbyte * vbytes` undercharges: the real transaction is larger, so `fee() / real_vbytes < fee_per_vbyte`. The caller's requested fee rate is silently not achieved.
2. The minimum-relay-fee guard at lines 211-213 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the same understated `vbytes`. A transaction carrying `data` can pass this check while its true fee rate is below the default minimum relay fee, producing a signed transaction that standard nodes will not relay or that sits unconfirmed — funds become unspendable in practice until the transaction is evicted/replaced.

### Impact Explanation
An attacker or even an ordinary caller supplying `data` (an untrusted, user-controlled byte vector passed to `new`) causes the produced transaction to pay less than the requested fee rate, and potentially less than the network minimum relay fee. For a threshold wallet this means the signed transaction is non-relayable/stuck: the inputs are committed to this exact TX via `Prevouts::All` sighash (lines 373-390), so the outputs/funds referenced are effectively frozen until a replacement is signed — the practical analog of "value credited/moved incorrectly because the formula omitted the added term." Where Surge over-minted shares, here the protocol under-pays for the bytes it actually adds.

### Likelihood Explanation
`data` is a public caller-supplied input to `SignableTransaction::new` (up to 80 bytes is explicitly allowed by the check at line 171). Any call that includes data and a tight `fee_per_vbyte` (e.g., exactly the minimum relay rate) deterministically produces an under-priced transaction. No collusion, no validator misbehavior, and no malformed encodings are required — just a parameter the API accepts.

### Recommendation
Include the OP_RETURN output when estimating weight. Either build the template transaction from the final `tx_outs` (including the `ScriptBuf::new_op_return` output) or pass the data output into `calculate_weight_vbytes` alongside `payments`, in both the no-change (line 204) and change (line 226) calculations:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), &tx_outs_as_pairs, None);
```

i.e., make the fee denominator reflect every output actually appended to the transaction, including `data` and `change`.

### Proof of Concept
1. Construct `SignableTransaction::new(inputs, payments, change: None, data: Some(vec![0u8; 80]), fee_per_vbyte)` where `fee_per_vbyte` is chosen so `fee_per_vbyte * vbytes == DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` boundary (e.g., `fee_per_vbyte = 1`).
2. The OP_RETURN output adds ~89 serialized bytes (~89 vbytes after rounding) that `calculate_weight_vbytes` never counts.
3. The resulting `tx` is ~89 vbytes larger than estimated; `fee() / actual_vbytes` is below 1 sat/vbyte — under the default minimum relay fee — while `new` returned `Ok`, having passed the `TooLowFee` check on the understated `vbytes`. The fully-signed transaction is then rejected/dropped by standard relay policy, leaving the spent inputs' funds unmovable via this transaction.

Relevant code: `networks/bitcoin/src/wallet/send.rs` lines 194-213 (data output pushed, then weight/fee computed from `payments` only), lines 224-235 (change path repeats the omission), and `calculate_weight_vbytes` at lines 62-99 (template outputs exclude `data`).