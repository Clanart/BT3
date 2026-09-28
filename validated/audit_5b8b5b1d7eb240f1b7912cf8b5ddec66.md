### Title
Swapped sender/recipient in DKG blame verification fatally slashes the honest accuser instead of the faulty share sender - ([File: processor/src/key_gen.rs])

### Summary
The Grafana advisory is an authorization failure: an action (delete) is performed on a resource without checking that the actor has rights over it. The Serai analog is in the DKG blame pipeline: when a participant reports an invalid share, `CoordinatorMessage::VerifyBlame` invokes `AdditionalBlameMachine::blame` with `(sender, recipient)` arguments swapped, so the blame-determination logic always attributes fault to the accusing recipient rather than the accused sender. The whole validator set then votes `RemoveParticipantDueToDkg` against the innocent reporter — an unprivileged participant can cause the removal/slashing of a target they have no legitimate power over, purely by sending that target a malformed share.

### Finding Description
In `processor/src/key_gen.rs`, the `VerifyBlame` handler evaluates blame on both the Ristretto (substrate) and network curves:

```rust
let substrate_blame = AdditionalBlameMachine::new(...)
    .unwrap()
    .blame(accuser, accused, substrate_share, substrate_blame);
let network_blame = AdditionalBlameMachine::new(...)
    .unwrap()
    .blame(accuser, accused, network_share, network_blame);
```

But `BlameMachine::blame` in `crypto/dkg/pedpop/src/lib.rs` is declared as `blame(self, sender: Participant, recipient: Participant, msg, proof)` — `sender` is the party who *sent* the encrypted share (i.e., `accused`), and `recipient` is the party who received it (i.e., `accuser`). The tests confirm this ordering: `machine.blame(ONE, TWO, ...)` where `ONE` sent the share to `TWO`.

The swap is fatal to the verdict because `blame_internal` → `Decryption::decrypt_with_proof` verifies the per-message Schnorr proof of possession with `pop_challenge(context, ..., from = sender, msg)`, and `encrypt()` binds `from` to the real sender's `Participant` index at encryption time. With `sender = accuser`, the PoP challenge is computed over the wrong participant index, `msg.pop.verify` fails, `DecryptionError::InvalidSignature` is returned, and `blame_internal` returns `sender` — which is the **accuser**.

Back in `key_gen.rs`:

```rust
if (substrate_blame == accused) || (network_blame == accused) {
    return ProcessorMessage::Blame { id, participant: accused };
}
ProcessorMessage::Blame { id, participant: accuser }
```

Since `blame` deterministically returns `accuser`, every `VerifyBlame` produces `Blame { participant: accuser }`. The coordinator (`coordinator/src/main.rs:537-549`) maps this to `Transaction::RemoveParticipantDueToDkg` naming the accuser, and `coordinator/src/tributary/handle.rs:258-284` accumulates votes until the threshold `t` triggers `fatal_slash` on that participant.

### Impact Explanation
- An honest validator who receives a malformed DKG share and reports it via `InvalidDkgShare` is the party blamed by every correctly-functioning processor, collects `t` removal votes, and is fatally slashed / removed from the set.
- The genuinely faulty sender is never blamed: a real invalid share produces the exact same `InvalidSignature` early-return path, so the accusation boomerangs onto the victim 100% of the time.
- An attacker controlling a single key share can therefore force the removal of any chosen co-participant in the DKG — repeated across attempts, a minority attacker can strip honest participants one at a time. As `RemovedAsOfDkgAttempt` shrinks `n`, the attacker can also drive the remaining set toward/below the safety margin, degrading the threshold's Byzantine tolerance and seizing stake via slashing.

### Likelihood Explanation
- Reachable with purely public/protocol inputs: the attacker only has to publish one malformed share encrypted to the victim (or a message whose PoP is valid but content is corrupt). No collusion, leaked keys, or malicious infrastructure is needed — each additional vote comes from honest validators executing the buggy `VerifyBlame` deterministically.
- The bug is unconditional: argument order is fixed at compile time, so every blame resolution returns the accuser. There is no edge case where an accusation resolves correctly.

### Recommendation
- In `processor/src/key_gen.rs` `VerifyBlame`, call `.blame(accused, accuser, substrate_share, substrate_blame)` and `.blame(accused, accuser, network_share, network_blame)` — the accused is the share *sender*, the accuser is the *recipient*.
- Add a regression test in `crypto/dkg/pedpop/src/tests.rs` exercising the `AdditionalBlameMachine` path used by `VerifyBlame` (blame evaluated by a third party with `sender=accused`, `recipient=accuser`), asserting the sender is blamed for an invalid share and the recipient for a false accusation.
- Consider renaming `blame`'s parameters to `sender`/`recipient` at the call site or making `VerifyBlame` fields `sender`/`recipient` to prevent reintroduction.

### Proof of Concept
1. Run a `t`-of-`n` PedPoP DKG. Participant `A` (attacker) computes its share vector but encrypts garbage to victim `V` — e.g., a ciphertext that fails `C::F::from_repr`, or simply a message whose embedded share fails `share_verification_statements` (see `invalid_share_value_blame` in `crypto/dkg/pedpop/src/tests.rs:315-346` for a template of producing such a share).
2. `V`'s `KeyMachine::calculate_share` fails with `PedPoPError::InvalidShare { participant: A, blame }`, so `V`'s processor emits `ProcessorMessage::InvalidShare { accuser: V, faulty: A, blame }`, which the coordinator publishes as `Transaction::InvalidDkgShare`.
3. `handle_application_tx` accepts it (accuser `V` is in `signed.signer`'s range; `DkgShare::get(genesis, V, A)` exists) and dispatches `CoordinatorMessage::VerifyBlame` to every processor.
4. Every processor runs `.blame(V, A, share, proof)`. `decrypt_with_proof(from=V, ...)` computes `pop_challenge` with `sender=V`, while `A` bound `from=A` at encryption → `msg.pop.verify` fails → `DecryptionError::InvalidSignature` → `blame_internal` returns `sender=V`.
5. `substrate_blame == V != A` and `network_blame == V != A`, so the processor returns `Blame { participant: V }`. Each validator publishes `RemoveParticipantDueToDkg { participant: V }`; once votes reach `t`, `fatal_slash` executes on `V`.

The correct outcome — `A` blamed — is unreachable because the same call returns `accuser` for every input, including genuinely invalid shares and fabricated accusations.