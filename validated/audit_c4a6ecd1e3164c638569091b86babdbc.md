### Title
`SignableTransaction` trusts deserialized `ReceivedOutput` values, double-counting inputs and signing consensus-invalid transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes `input_sat` and the `Prevouts::All` sighash commitment directly from the `output.value`/`outpoint` fields of each supplied `ReceivedOutput`, with no deduplication of outpoints and no chain verification. Since `ReceivedOutput::read` accepts fully attacker-controlled bytes (arbitrary `offset`, `TxOut`, `OutPoint`), an unprivileged party can supply duplicate or phantom inputs whose value is counted as spendable funds, exactly mirroring the RevenueHandler bug where the whole token balance (including already-claimed amounts) was counted as new revenue.

### Finding Description
The analog bug class is *stale/duplicated value being counted as fresh spendable value*:

- `ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` straight from untrusted bytes with no validation that the outpoint exists or that the value is real (`networks/bitcoin/src/wallet/mod.rs:122-134`).
- `SignableTransaction::new` sums `input.output.value` into `input_sat` (`send.rs:175`), uses it for the `NotEnoughFunds` check (`send.rs:215`), stores the claimed `TxOut`s into `prevouts` (`send.rs:253`), and never checks that outpoints are unique (`send.rs:177-185`).
- `multisig` only verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` (`send.rs:277`); a *duplicate* of a legitimate received output trivially passes this check, as does a phantom output whose script is set to the group key's script.
- `sign` commits `Prevouts::All(&self.tx.prevouts)` into every input's `taproot_key_spend_signature_hash` (`send.rs:375-390`), so the entire claimed prevout set — including doubled or fictitious values — is signed.

The result: the same UTXO counted twice inflates `input_sat` (RevenueHandler's inflated `epochRevenues`), and the threshold produces a complete, well-formed signature set for a transaction that is consensus-invalid (duplicate input spends the same outpoint twice) or references non-existent prevouts.

### Impact Explanation
An attacker who can feed bytes into `ReceivedOutput::read` (e.g., a peer/relayer supplying input lists, or any path deserializing outputs from untrusted storage/network data) can cause the FROST multisig to:
1. Pass the `NotEnoughFunds` check using double-counted or phantom value.
2. Burn a full threshold signing round (preprocess + sign + complete) producing a transaction that can never be confirmed — the real inputs remain unspent while the signing session is consumed.

This is a griefing/liveness attack on payout construction: funds are "accounted" (and fees computed) against value that was already spent or never existed, and every honest signer's effort is wasted on an unbroadcastable transaction. If the caller retries naively with the same corrupted input list, payouts stall indefinitely. Severity: Medium.

### Likelihood Explanation
Reachability depends on whether attacker-influenced bytes reach `ReceivedOutput::read` / `SignableTransaction::new`. The struct is `pub`, `read` is `pub`, and the wallet crate exposes no other authentication of an output's on-chain existence — `multisig`'s script check is explicitly the only validation (`send.rs:277`). Any integrator or protocol path that transports `ReceivedOutput` over an untrusted channel (its `serialize`/`read` pair exists precisely for that) is exposed. The attack requires no key material and no collusion.

### Recommendation
- Deduplicate inputs by `outpoint` in `SignableTransaction::new` and reject duplicates.
- Verify each `prevouts[i]` value against the chain (or only accept `ReceivedOutput`s produced by `Scanner::scan_transaction` on observed blocks), making the provenance requirement part of the type (e.g., a constructor-restricted type rather than a publicly readable/deserializable one).
- Optionally commit the outpoint set into `multisig`'s key/offset check so a `ReceivedOutput` with a mismatched `outpoint` fails early.

### Proof of Concept
Conceptual test against the crate API (regtest context as in `networks/bitcoin/tests/wallet.rs`):

```rust
// scanner receives a real output `o` for the group key
let o: ReceivedOutput = send_and_get_output(&rpc, &scanner, key).await;

// Attacker serializes it and feeds duplicated bytes back through read()
let mut dup_bytes = o.serialize();
let dup = ReceivedOutput::read(&mut dup_bytes.as_slice()).unwrap();

// The same outpoint is counted twice
let tx = SignableTransaction::new(
  vec![o.clone(), dup],                       // duplicate outpoint
  &[(payment_script, o.value() * 2 - FEE * 200)], // payment only affordable via double-count
  None, None, FEE,
).unwrap(); // passes NotEnoughFunds because input_sat == 2 * o.value()

// Threshold signs it successfully; the resulting tx has two identical TxIns
let signed = sign(&keys, &tx);
assert_eq!(signed.input[0].previous_output, signed.input[1].previous_output);
// -> consensus-invalid: the same outpoint is spent twice; the signature
//    round was wasted and no funds move.
```