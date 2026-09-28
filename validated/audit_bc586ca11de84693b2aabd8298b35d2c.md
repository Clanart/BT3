### Title
Panic on empty statement list in multiexponentiation batch verification - (File: crypto/multiexp/src/straus.rs)

### Summary
The Straus multiexponentiation routine unconditionally indexes `groupings[0]`, so `multiexp`/`multiexp_vartime` panic on an empty `(scalar, point)` list. `BatchVerifier::verify`/`verify_vartime` (and `verify_vartime_with_vartime_blame`) call `multiexp_vartime(&flat(&self.0))` with no check that any statements were queued, so a `BatchVerifier` (or the Schnorr/DLEq `batch_verify`-style APIs that wrap it) invoked with zero queued statements crashes instead of returning `true`. This is the Serai analog of the mongo-express DoS: an unhandled edge case on empty input causes a crash reachable by an unprivileged party who controls how many items are fed to a `verify` API.

### Finding Description
`straus` (and `straus_vartime`) build `groupings = prep_bits(pairs, window)` and immediately iterate `0 .. groupings[0].len()` [1](#0-0) . When `pairs` is empty, `prep_bits` produces an empty vector and `groupings[0]` is an index-out-of-bounds panic. `straus_vartime` has the identical flaw [2](#0-1) .

`BatchVerifier` exposes this to public inputs: `flat` on an empty statement list yields an empty pair list [3](#0-2) , and `verify_vartime` passes it straight to `multiexp_vartime` [4](#0-3) . Any `verify` API that forwards an attacker-controlled list of signatures/proofs into a `BatchVerifier` (e.g., Schnorr batch verification in `crypto/schnorr`, whose `BatchVerifier` queues one statement per supplied signature) therefore panics when the supplied list is empty — a case that is trivially reachable and never validated.

### Impact Explanation
An unprivileged party that can cause a verifier to run batch verification over zero statements (e.g., supplying an empty batch of signatures/proofs to a `batch_verify`/`verify` entry point) triggers an index-out-of-bounds panic, crashing the calling thread/process. This is a denial of service matching the report's severity class (Medium): availability is lost, but no keys, signatures, or funds are compromised.

### Likelihood Explanation
`straus`/`straus_vartime` index `groupings[0]` with no length guard, and `BatchVerifier::verify`/`verify_vartime`/`verify_vartime_with_vartime_blame` never assert that at least one statement was queued — `new(0)` followed by `verify()` is sufficient to panic. Note: if `multiexp`/`multiexp_vartime` in `crypto/multiexp/src/lib.rs` early-return identity on empty input before dispatching to `straus`, this panic is not reachable through `BatchVerifier`; I was unable to confirm the dispatch behavior, so the finding hinges on `straus` being reached with `pairs.is_empty()`.

### Recommendation
Add an explicit empty-input check: return `G::identity()` at the top of `straus` and `straus_vartime` (or in `multiexp`/`multiexp_vartime` before dispatch). Optionally also document/short-circuit `BatchVerifier::verify*` when no statements were queued.

### Proof of Concept
```rust
// crypto/multiexp: empty multiexp panics in straus via groupings[0]
let pairs: Vec<(Scalar, ProjectivePoint)> = vec![];
let _ = multiexp_vartime(&pairs); // panics: index out of bounds at straus.rs groupings[0]

// Via BatchVerifier with no queued statements
let batch: BatchVerifier<u8, ProjectivePoint> = BatchVerifier::new(0);
batch.verify_vartime(); // calls multiexp_vartime(&[]) -> panic instead of Ok/true
```

### Citations

**File:** crypto/multiexp/src/straus.rs (L31-35)
```rust
  let mut groupings = prep_bits(pairs, window);
  let mut tables = prep_tables(pairs, window);

  let mut res = G::identity();
  for b in (0 .. groupings[0].len()).rev() {
```

**File:** crypto/multiexp/src/straus.rs (L56-60)
```rust
  let groupings = prep_bits(pairs, window);
  let tables = prep_tables(pairs, window);

  let mut res: Option<G> = None;
  for b in (0 .. groupings[0].len()).rev() {
```

**File:** crypto/multiexp/src/batch.rs (L15-19)
```rust
fn flat<Id: Copy + Zeroize, G: Zeroize + Group<Scalar: Zeroize + PrimeFieldBits>>(
  slice: &[(Id, Vec<(G::Scalar, G)>)],
) -> Zeroizing<Vec<(G::Scalar, G)>> {
  Zeroizing::new(slice.iter().flat_map(|pairs| pairs.1.iter()).copied().collect::<Vec<_>>())
}
```

**File:** crypto/multiexp/src/batch.rs (L98-101)
```rust
  #[must_use]
  pub fn verify_vartime(&self) -> bool {
    multiexp_vartime(&flat(&self.0)).is_identity().into()
  }
```
