### Title
Malformed transaction signature-share vector causes an unhandled panic - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`TransactionSignatureMachine::complete` trusts every submitted `Vec<SignatureShare<Secp256k1>>` to contain one share for each transaction input. When completing the first input, it calls `shares.remove(0)` without checking that the vector is non-empty.

A participant can therefore submit a syntactically valid share collection containing an empty inner vector for their participant ID. `complete` then panics instead of returning `FrostError::InvalidShare`, causing a remotely reachable denial of service.

### Finding Description
`TransactionSignatureMachine::complete` iterates over each transaction input and converts the outer participant map into the per-input share map expected by `AlgorithmSignatureMachine::complete`:

```rust
schnorr.complete(
  shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
)
```

`Vec::remove(0)` panics when the inner vector is empty. There is no preceding validation that every participant supplied exactly `self.sigs.len()` shares.

The same unchecked vector-shape assumption also exists in `TransactionSignMachine::sign`, where a participant-controlled `Vec<Preprocess<Secp256k1, ()>>` is indexed with `commitments[c]` without first checking its length.

### Impact Explanation
An unprivileged signing participant can submit an empty or truncated per-input share vector during transaction completion. The victim's signing process panics while aggregating shares for the first input.

This prevents completion of the transaction and can terminate the calling task or process. Because the panic occurs before signature verification, no valid share or cryptographic knowledge is required.

### Likelihood Explanation
The triggering input is structurally simple: the outer `HashMap` contains the participant, but that participant's inner share vector has length zero. The protocol accepts participant-selected share collections in `complete`, and the implementation assumes transport framing always supplies one scalar per input.

`read_share` itself reads a fixed number of shares and would reject truncated bytes. However, `complete` is a public API boundary and directly accepts attacker-supplied nested vectors. Any integration that constructs these vectors from participant messages can pass a malformed collection and trigger the panic.

### Recommendation
Validate the nested message length before indexing or removing elements.

For `TransactionSignatureMachine::complete`:

- Require every submitted vector to contain exactly `self.sigs.len()` shares.
- Return `FrostError::InvalidShare(participant)` or `FrostError::InvalidParticipantQuantity` when the length differs.
- Avoid `Vec::remove(0)`; consume or drain the vector only after validating its length.

For `TransactionSignMachine::sign`:

- Require each participant's preprocess vector to contain exactly `self.sigs.len()` entries before indexing it.
- Return `FrostError::InvalidPreprocess(participant)` or `FrostError::InvalidParticipantQuantity` on mismatch.

### Proof of Concept
After creating a Bitcoin `TransactionSignatureMachine` for a transaction with at least one input, provide a share map whose participant entry has no inner signature shares:

```rust
use std::collections::HashMap;
use frost::{Participant, sign::SignatureMachine};

let peer = Participant::new(2).unwrap();

let malformed_shares = HashMap::from([
  (peer, Vec::new()),
]);

let _ = transaction_signature_machine.complete(malformed_shares);
```

Execution reaches `shares.remove(0)` inside `TransactionSignatureMachine::complete` and panics with `removal index (is 0) should be < len (is 0)`, instead of returning a `FrostError`.