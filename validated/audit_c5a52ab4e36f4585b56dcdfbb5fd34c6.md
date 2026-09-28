### Title
Swapped `sender`/`recipient` arguments in `VerifyBlame` cause valid blame reports to slash the honest accuser instead of the malicious sender - (File: processor/src/key_gen.rs)

### Summary
`CoordinatorMessage::VerifyBlame` in `processor/src/key_gen.rs:504-564` verifies PedPoP blame proofs by calling `AdditionalBlameMachine::blame(accuser, accused, ...)`, but the `blame`/`blame_internal` API in `crypto/dkg/pedpop/src/lib.rs:674-682` takes `(sender, recipient, ...)`. The arguments are transposed: the accuser (the share recipient) is passed as `sender`, and the accused (the share sender) is passed as `recipient`. Like the picklescan flaw where a dangerous function goes undetected because the checker's coverage is incomplete, here the blame-determination check is invoked on the wrong roles, so the "dangerous" participant is never the one evaluated.

### Finding Description
`blame` is documented and implemented so that `sender` is "the sender, who sent an invalid secret share" and `recipient` is "the receiver, who claimed a valid secret share was invalid" (`crypto/dkg/pedpop/src/lib.rs:611-617`). Internally, `blame_internal` calls `Decryption::decrypt_with_proof(sender, recipient, msg, proof)` (`crypto/dkg/pedpop/src/lib.rs:582`), which verifies the message's proof-of-possession using `from = sender` and the ECDH decryption key registered under `decryptor = recipient` (`crypto/dkg/pedpop/src/encryption.rs:366-397`).

In `processor/src/key_gen.rs:549` and `:556`, the coordinator instead calls:

```rust
.blame(accuser, accused, substrate_share, substrate_blame)
```

so `sender = accuser` and `recipient = accused`. The `EncryptedMessage`'s PoP was originally produced by the real sender (the accused) with `from = accused`. Evaluating the challenge with `from = accuser` makes `msg.pop.verify(...)` fail, yielding `DecryptionError::InvalidSignature`, which `blame_internal` maps to blaming `sender` — i.e., the accuser (`crypto/dkg/pedpop/src/lib.rs:585`, `crypto/dkg/pedpop/src/encryption.rs:374-379`).

The coordinator then checks `if (substrate_blame == accused) || (network_blame == accused)` (`key_gen.rs:559`); since both evaluations return `accuser`, it emits `ProcessorMessage::Blame { id, participant: accuser }` (`key_gen.rs:563`).

### Impact Explanation
A participant who correctly detects an invalid secret share and submits a blame report has their own `blame` evaluation rigged against them: the PoP check is recomputed under the wrong `from`, fails deterministically, and the accuser is reported as the faulty party and fatally slashed. Simultaneously, the malicious DKG participant who sent the malformed/invalid share is exonerated — the exact analog of picklescan missing a dangerous function: the detection machinery exists but is applied to the wrong subject, so malicious input evades identification. This destroys the economic security of the DKG blame mechanism (honest reporters are punished, cheaters are unpunished) and can be used to deter or drain honest validators while corrupt shares go unblamed.

### Likelihood Explanation
Deterministic and reachable by any DKG participant: the accuser submits `VerifyBlame` through normal coordinator message flow with attacker/public-supplied bytes (`share`, `blame`). The swap guarantees `pop.verify` fails on every legitimate accusation because the `from` participant bound into `pop_challenge` never matches. Note this also means a *malicious* accuser can frame the accused only when... the PoP still fails (since `from=accuser` is fixed), so every accusation deterministically blames the accuser — always exploitable, no preconditions beyond participating in key generation.

### Recommendation
Swap the call sites to `.blame(accused, accuser, share, blame)` so `sender = accused` (the party who authored the `EncryptedMessage`) and `recipient = accuser` (the party whose encryption key decrypts it), matching the `blame_internal` contract. Alternatively, rename the parameters in `ProcessorMessage::VerifyBlame` handling (`sender`/`recipient` instead of `accused`/`accuser`) to prevent the transposition.

### Proof of Concept
1. During a PedPoP DKG, participant `TWO` sends participant `ONE` an encrypted share whose scalar is invalid (or whose share-verification statements fail), exactly as in the existing `invalid_share_value_blame` test (`crypto/dkg/pedpop/src/tests.rs:315-345`), which shows `blame(ONE/*sender*/, TWO/*recipient*/, ...)` correctly blames `ONE`.
2. `ONE` reports `CoordinatorMessage::VerifyBlame { accuser: ONE, accused: TWO, share, blame }` to the coordinator.
3. In `key_gen.rs:549`, `blame(ONE, TWO, substrate_share, ...)` invokes `decrypt_with_proof(sender=ONE, decryptor=TWO, ...)`. `pop.verify` is evaluated with `pop_challenge(.., from=ONE, ..)` while the message's PoP was created with `from=TWO`, so verification fails → `InvalidSignature` → `blame_internal` returns `sender = ONE` (the accuser).
4. Both substrate and network evaluations return `ONE`; the check `substrate_blame == accused` is false; the coordinator emits `ProcessorMessage::Blame { participant: ONE }` — slashing the honest reporter while `TWO`, who sent the invalid share, escapes blame entirely.