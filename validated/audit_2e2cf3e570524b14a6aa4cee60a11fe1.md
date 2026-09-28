### Title
Forged `ReceivedOutput` values inflate the accounted input sum, producing an unspendable/over-promising transaction - (File: networks/bitcoin/src/wallet/send.rs, networks/bitcoin/src/wallet/mod.rs)

### Summary

The Arcadia bug is an *uncapped ratio assumed to be bounded*: utilization = borrowed / loaned exceeds 100% because an attacker donates tokens to inflate the effective numerator while nothing validates the true pool balance. The analog in bitcoin-serai is the same shape: `SignableTransaction::new` computes `input_sat` by **trusting the `value` field embedded in each `ReceivedOutput`**, and `ReceivedOutput::read` deserializes that value verbatim from untrusted bytes with no check that the referenced outpoint exists or holds the claimed amount. Nothing caps or verifies the "donated" input value against reality, so the accounting sum is manipulable arbitrarily above the true balance — directly analogous to utilization going above 100%.

### Finding Description

`ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) reads `offset`, `output` (a full `TxOut` including `value`), and `outpoint` from a reader, with no validation beyond consensus-decodability. The only downstream check is in `SignableTransaction::multisig` (`networks/bitcoin/src/wallet/send.rs:273-284`), which verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — i.e., it binds the *script* to the offset key, but never the *amount*. In `SignableTransaction::new`:

- `input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum()` (`send.rs:175`)
- Change amount: `input_sat - (payment_sat + fee_with_change)` is pushed as a real output when ≥ `DUST` (`send.rs:224-235`)
- `fee()` returns `sum(prevouts) - sum(outputs)` (`send.rs:138-141`)

An attacker supplying a `ReceivedOutput` whose `output.value` is inflated (a "donation" into the accounting, exactly like donating tokens to the lending pool) pushes `input_sat` far above the true balance. Because Taproot sighash commits to all prevout amounts (`Prevouts::All` semantics in the signing machine), the forged amount is baked into the signed transaction: the resulting transaction's claimed inputs do not cover its outputs on-chain (`bad-txns-in-belowout` / nonexistent UTXO), so every node rejects it — yet the signers have consumed a FROST signing session and the scheduler's plan is marked spent.

### Impact Explanation

- If the outpoint is real but the value forged higher: the signed transaction commits (via Taproot sighash prevout commitments) to prevout amounts that don't match the chain — the transaction is invalid and unbroadcastable. Funds are stuck in a completed-but-unconfirmable plan; the change/payment outputs promise more than exists. Analogous to Arcadia's future depositors being drained: downstream logic (fee amortization in `created_output`, change crediting) acts on phantom sats.
- If the outpoint is fabricated entirely: same result — a signed, permanently invalid transaction; the multisig session and nonce set are burned (ROS-style reuse concerns aside, the plan must be rebuilt).
- Worst case, if any consumer trusts `ReceivedOutput` values for balance/credit reporting without on-chain re-verification, phantom balances are credited — "funds reported received that are not spendable."

### Likelihood Explanation

Reachable by any party able to feed bytes to `ReceivedOutput::read` (the listed untrusted-bytes surface) or to construct a `SignableTransaction` from attacker-influenced `ReceivedOutput`s — e.g., a processor/validator path where scanned or relayed outputs are serialized and consumed by a peer/coordinator. It requires no threshold collusion, no malicious validator majority, and no key leakage: just one forged amount field. Realization of full theft requires a consumer that credits the phantom value off-chain; the guaranteed impact is a DoS/invalid-transaction plus misaccounting, matching the original report's Medium severity.

### Recommendation

Verify prevout amounts before accounting: require each `ReceivedOutput` to carry (or be resolved against) on-chain proof of its value — e.g., fetch the prevout from the chain/RPC and compare `output.value` and `outpoint` before `input_sat` is computed, or reject any `SignableTransaction` where `fee()` exceeds a bounded, sane maximum relative to `needed_fee` (the analog of a utilization cap). At minimum, treat `ReceivedOutput::read` output values as untrusted and validate them at the `multisig`/`new` boundary, the same way `script_pubkey` is already bound to the offset key at `send.rs:277`.

### Proof of Concept

```rust
// Conceptual PoC against networks/bitcoin/src/wallet
// Attacker-controlled bytes fed to ReceivedOutput::read:
//   offset = Scalar::ZERO-encoded 32 bytes
//   output = TxOut { value: 1_000_000_000 sat (claimed), script_pubkey: <pool p2tr script> }
//   outpoint = <a real outpoint holding only 1_000 sat, or a fabricated one>
let forged = ReceivedOutput::read(&mut attacker_bytes).unwrap();

let stx = SignableTransaction::new(
    vec![forged],                      // input_sat = 1_000_000_000 (phantom)
    &[(payment_script, 600_000_000)],  // passes NotEnoughFunds check via forged value
    Some(change_script),               // change output minted for ~400M phantom sats
    None,
    fee_per_vbyte,
).unwrap();

// multisig() succeeds: it only checks script_pubkey vs offset group key (send.rs:277)
// sign() produces a valid FROST signature committing to prevout amounts via Taproot sighash
// Broadcast: rejected network-wide (bad-txns-in-belowout / missing UTXO)
// Downstream: scheduler amortizes/credits against phantom sats -> misaccounting + stuck plan
```