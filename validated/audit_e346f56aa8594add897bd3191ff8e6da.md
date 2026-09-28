### Title
Missing participant in `GeneratorPromotion::complete` causes unwrap panic, aborting key promotion - (File: crypto/dkg/promote/src/lib.rs)

### Summary
CVE-2023-21872 is an availability flaw: a network-reachable, crafted input causes a repeatable crash (complete DoS) of the target service. The analog in Serai is a reachable panic in `GeneratorPromotion::complete` in `crypto/dkg/promote/src/lib.rs`. The function iterates participants `1 ..= n` and calls `proofs.get(&i).unwrap()` [1](#0-0) , assuming every participant index is present in the attacker-influenced `proofs` map. Only the map's length (`n - 1`) and each key's upper bound (`<= n`) are checked [2](#0-1) . A map of length `n - 1` that omits some index `j` (e.g., by containing a proof keyed under the local participant's own index `params.i()`) causes `proofs.get(&j).unwrap()` to panic.

### Finding Description
`complete` performs two validations:
- `proofs.len() == n - 1` [3](#0-2) 
- every key satisfies `u16::from(i) <= params.n()` [4](#0-3) 

Neither check guarantees that `proofs` contains exactly the set `{1..=n} \ {params.i()}`. Specifically:
- No check rejects `params.i()` as a key in `proofs`.
- Because `Participant` is only constrained to be nonzero [5](#0-4) , a map keyed `{params.i()} ∪ S` where `S` omits some honest index `j` still passes both checks.

The loop then iterates `i` over `1 ..= params.n()`, skipping only `params.i()`, and unconditionally unwraps [6](#0-5) :

```rust
let proof = proofs.get(&i).unwrap();
```

When `i` reaches the omitted index `j`, `proofs.get(&j)` returns `None` and the `.unwrap()` panics, crashing the calling thread/process mid-protocol. Contrast with `pedpop`, which uses `validate_map` to verify the map contains every expected participant before indexing [7](#0-6) , and `dkg`'s own `DkgError::MissingParticipant`/`DuplicatedParticipant` error variants [8](#0-7)  — the machinery for a graceful error exists but is not used here.

### Impact Explanation
Any party able to supply a `GeneratorProof` entry to `GeneratorPromotion::complete` — i.e., any participant contributing proofs during a generator promotion — can deterministically panic the process. This is a complete, repeatable denial of service of the key-promotion path: the node aborts instead of returning `PromotionError`, so the promotion can never finalize and, depending on how the integrator drives the state machine, the crash propagates to whatever runtime hosts it. This matches the CVE's availability-impact class (hang or frequently repeatable crash) mapped onto Serai's shape: untrusted peer data fed to a `complete` API triggers an unconditional panic rather than a typed error.

### Likelihood Explanation
- Reachability: `complete` is a public API taking an externally populated `HashMap<Participant, GeneratorProof<C1>>`; no private state or secret is needed to trigger it.
- Preconditions: a participant (or anyone able to influence the keys of the `proofs` map, e.g., a mislabeled/misrouted proof message) registers a proof under `params.i()` or otherwise omits an index. This requires no threshold collusion — a single malformed map entry suffices.
- The `proofs.len() == n - 1` check is satisfiable while omitting an index precisely because the forbidden-but-unchecked key `params.i()` provides a spare slot.

### Recommendation
Before the loop, validate the map membership rather than relying on length/bounds. Either reuse the `validate_map`-style check (assert every `l` in `1..=n`, `l != params.i()`, has an entry and that `params.i()` is absent), or replace `proofs.get(&i).unwrap()` with `proofs.get(&i).ok_or(PromotionError::MissingParticipant(i))?`, adding a corresponding error variant. A defense-in-depth `DuplicatedParticipant`/`InvalidParticipant` error for `i == params.i()` keys would also harden the API.

### Proof of Concept
```rust
// Assume a t-of-n promotion with params.i() = 1, n = 3.
// proofs is built from received GeneratorProof messages keyed by sender.
let mut proofs: HashMap<Participant, GeneratorProof<C1>> = HashMap::new();
// Attacker/misrouted entries: includes self-index 1, omits index 3.
proofs.insert(Participant::new(1).unwrap(), attacker_proof_a);
proofs.insert(Participant::new(2).unwrap(), attacker_proof_b);
// proofs.len() == 2 == n - 1; all keys <= n -> passes both checks.
// Loop hits i = 3, proofs.get(&3) == None -> panic on .unwrap()
let _ = promotion.complete(&proofs); // thread panics; promotion aborted
```

The panic is deterministic, requires no secrets, and aborts instead of surfacing a `PromotionError` [9](#0-8) .

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L125-136)
```rust
    if proofs.len() != (usize::from(params.n()) - 1) {
      Err(PromotionError::IncorrectAmountOfParticipants {
        t: params.n(),
        n: params.n(),
        amount: proofs.len() + 1,
      })?;
    }
    for i in proofs.keys().copied() {
      if u16::from(i) > params.n() {
        Err(PromotionError::InvalidParticipant { n: params.n(), participant: i })?;
      }
    }
```

**File:** crypto/dkg/promote/src/lib.rs (L140-154)
```rust
    for i in 1 ..= params.n() {
      let i = Participant::new(i).unwrap();
      if i == params.i() {
        continue;
      }

      let proof = proofs.get(&i).unwrap();
      proof
        .proof
        .verify(
          &mut transcript(&self.base.original_group_key(), i),
          &[C1::generator(), C2::generator()],
          &[self.base.original_verification_share(i), proof.share],
        )
        .map_err(|_| PromotionError::InvalidProof(i))?;
```

**File:** crypto/dkg/src/lib.rs (L27-35)
```rust
impl Participant {
  /// Create a new Participant identifier from a u16.
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }
```

**File:** crypto/dkg/src/lib.rs (L110-116)
```rust
  /// A participant was duplicated.
  #[error("a participant ({0}) was duplicated")]
  DuplicatedParticipant(Participant),

  /// Not participating in declared signing set.
  #[error("not participating in declared signing set")]
  NotParticipating,
```

**File:** crypto/dkg/pedpop/src/lib.rs (L56-83)
```rust
// Validate a map of values to have the expected included participants
fn validate_map<T, C: Ciphersuite>(
  map: &HashMap<Participant, T>,
  included: &[Participant],
  ours: Participant,
) -> Result<(), PedPoPError<C>> {
  if (map.len() + 1) != included.len() {
    Err(PedPoPError::IncorrectAmountOfParticipants {
      expected: included.len(),
      found: map.len() + 1,
    })?;
  }

  for included in included {
    if *included == ours {
      if map.contains_key(included) {
        Err(PedPoPError::DkgError(DkgError::DuplicatedParticipant(*included)))?;
      }
      continue;
    }

    if !map.contains_key(included) {
      Err(PedPoPError::MissingParticipant(*included))?;
    }
  }

  Ok(())
}
```
