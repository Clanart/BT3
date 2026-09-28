### Title
Missing-participant panic in `GeneratorPromotion::complete` via attacker-controlled proof map key - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion::complete` indexes a caller-supplied `HashMap<Participant, GeneratorProof>` with `.unwrap()` after only checking the map's length and that each key is `<= n`. A counterparty can supply a proof set of the correct size that contains the victim's own participant index (or any duplicate-free set missing another required participant), causing `proofs.get(&i).unwrap()` to panic — a reachable denial of service mirroring the CVE's "wrong value passed where a valid one is required" class.

### Finding Description
In `complete`, the code validates:

1. `proofs.len() == n - 1` (crypto/dkg/promote/src/lib.rs:125)
2. every key `<= params.n()` (crypto/dkg/promote/src/lib.rs:132-136)

It never checks that the keys cover exactly `1..=n` minus `params.i()`. The subsequent loop iterates `i in 1..=params.n()`, skips `params.i()`, and does `proofs.get(&i).unwrap()` (crypto/dkg/promote/src/lib.rs:140-146). If the supplied map contains an entry keyed by the victim's own index `params.i()` (or otherwise omits some required participant `j`), `proofs.get(&j)` returns `None` and `.unwrap()` panics. The proof labeled `params.i()` is silently ignored (the `continue` at line 142 skips it before any DLEq verification), so it need not be valid — any bytes decodeable as a `GeneratorProof` suffice.

### Impact Explanation
An unprivileged counterparty in the key-promotion protocol can crash the victim's promotion step at negligible cost by sending a `proofs` map whose keys satisfy the length and bounds checks but omit one required participant. This aborts the promotion (and, in a state machine running the protocol in-process, panics the caller's thread), preventing key regeneration/migration for that participant set.

### Likelihood Explanation
`GeneratorProof` values are produced and exchanged between participants by design (`promote` returns a proof to broadcast, `complete` consumes peers' proofs). The triggering input is fully attacker-controlled: the map key is just a `Participant` label chosen by the sender, and no valid proof content is needed since the panic precedes verification for the omitted index.

### Recommendation
Replace the `unwrap()` at crypto/dkg/promote/src/lib.rs:146 with a checked lookup returning `PromotionError::InvalidParticipant`/`IncorrectAmountOfParticipants` when a required participant's proof is absent, or pre-validate that `proofs.keys()` equals `{1..=n} \ {params.i()}` and reject entries keyed by `params.i()`.

### Proof of Concept
```rust
// Victim has params.i() == 1, n == 3.
// Attacker (participant 2) sends proofs keyed {1, 2} instead of {2, 3}:
// len == 2 == n-1 passes; all keys <= 3 passes.
// Loop: i=1 skipped (params.i()), i=2 found, i=3 -> proofs.get(&3) == None -> panic.
let mut proofs = HashMap::new();
proofs.insert(Participant::new(1).unwrap(), attacker_proof_a); // self-index, never verified
proofs.insert(Participant::new(2).unwrap(), attacker_proof_b);
let _ = promotion.complete(&proofs); // panics on missing participant 3
```