### Title
Attacker-supplied preprocess with fewer nonce commitments causes index-out-of-bounds panic (DoS) - (File: crypto/frost/src/nonce.rs)

### Summary
CVE-2017-8069's bug class is "a buffer/scatterlist assumed to have one layout actually spans multiple pieces, so indexing past the real boundary corrupts memory or crashes." The Serai analog lives in FROST's binding-factor aggregation: `BindingFactor::nonces` iterates over the *locally planned* nonce slots (`planned_nonces`) and blindly indexes every other participant's `commitments.nonces[n]` and `binding_factors[n]`. A remote participant's `Commitments` are attacker-controlled bytes (parsed via `Commitments::read` / `read_preprocess`), and nothing in `AlgorithmSignMachine::sign` verifies that each participant's preprocess carries the same nonce count the algorithm declared.

### Finding Description
In `crypto/frost/src/sign.rs`, `nonces` is taken from `self.params.algorithm.nonces()` (the local, honest expectation), then `B.nonces(&nonces)` is called [1](#0-0) . Inside `BindingFactor::nonces` (`crypto/frost/src/nonce.rs:194-212`), for each nonce index `n` in `0..planned_nonces.len()` it executes `commitments.nonces[n]` and `binding_factors.as_ref().unwrap()[n]` for **every** participant's commitments [2](#0-1) .

`calculate_binding_factors` generates only `binding.commitments.nonces.len()` factors per participant — i.e., the vector length is attacker-controlled [3](#0-2) . A malicious signer can therefore transmit a `Preprocess`/`Commitments` whose `nonces` list is shorter than the number of nonces the `Algorithm` requires (e.g., `Algorithm::nonces()` returns 2+ for multi-generator/multi-nonce schemes, but the attacker sends 1). Honest signers then index `commitments.nonces[1]` on a length-1 vector → `index out of bounds` panic. This is exactly the "multi-page scatterlist treated as one page" shape: a container iterated under the assumption that all pieces are uniformly sized, when one piece is short.

### Impact Explanation
An unprivileged counterparty in a FROST signing session can crash every honest signer that consumes its malicious preprocess by sending a `Commitments` blob with fewer nonces than the algorithm plans for. In Serai's deployment this halts threshold signing (panic inside `sign`), a denial of service matching the CVE's "system crash" impact. Rust's bounds checking prevents memory corruption, so impact is DoS rather than corruption.

### Likelihood Explanation
The attack requires only public-input reach: an attacker supplies preprocess bytes to a `sign`/`read_preprocess` path — explicitly in scope. Whether the panic is fully reachable depends on whether `Commitments::read` or the algorithm's `preprocess`/`process_addendum` validation enforces a minimum nonce count; the code shown in `sign.rs` performs no such length check before `B.nonces(&nonces)` is invoked [1](#0-0) . Single-nonce algorithms (planned_nonces.len() == 1) are not exploitable since a zero-length `nonces` vec would already fail deserialization/identity checks earlier; multi-nonce/multi-generator algorithms expose the vulnerable index.

### Recommendation
Before calling `B.nonces(&nonces)` (or inside `BindingFactor::nonces`/`calculate_binding_factors`), assert `commitments.nonces.len() == planned_nonces.len()` (and matching `generators` arity) for every participant, returning `FrostError::InvalidPreprocess` instead of panicking. Equivalently, enforce the required nonce count at `Commitments::read`/`read_preprocess` time.

### Proof of Concept
1. Instantiate a FROST `Algorithm` whose `nonces()` returns 2 nonce slots (multi-generator scheme).
2. As a malicious participant, serialize a `Preprocess` whose `Commitments.nonces` vector contains a single nonce entry instead of 2, and feed it to an honest `SignatureMachine::sign`/`read_preprocess` path alongside a valid participant index in `included`.
3. Honest signer reaches `BindingFactor::nonces` at `crypto/frost/src/nonce.rs:204` (`commitments.nonces[1]` on a 1-element vec) → index-out-of-bounds panic → crash.

### Citations

**File:** crypto/frost/src/sign.rs (L320-383)
```rust
    let nonces = self.params.algorithm.nonces();
    #[allow(non_snake_case)]
    let mut B = BindingFactor(HashMap::<Participant, _>::with_capacity(included.len()));
    {
      // Parse the preprocesses
      for l in &included {
        {
          self
            .params
            .algorithm
            .transcript()
            .append_message(b"participant", C::F::from(u64::from(u16::from(*l))).to_repr());
        }

        if *l == self.params.keys.params().i() {
          let commitments = self.preprocess.commitments.clone();
          commitments.transcript(self.params.algorithm.transcript());

          let addendum = self.preprocess.addendum.clone();
          {
            let mut buf = vec![];
            addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, commitments);
          self.params.algorithm.process_addendum(&view, *l, addendum)?;
        } else {
          let preprocess = preprocesses.remove(l).unwrap();
          preprocess.commitments.transcript(self.params.algorithm.transcript());
          {
            let mut buf = vec![];
            preprocess.addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, preprocess.commitments);
          self.params.algorithm.process_addendum(&view, *l, preprocess.addendum)?;
        }
      }

      // Re-format into the FROST-expected rho transcript
      let mut rho_transcript = A::Transcript::new(b"FROST_rho");
      rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
      rho_transcript.append_message(b"message", C::hash_msg(msg));
      rho_transcript.append_message(
        b"preprocesses",
        C::hash_commitments(self.params.algorithm.transcript().challenge(b"preprocesses").as_ref()),
      );

      // Generate the per-signer binding factors
      B.calculate_binding_factors(&rho_transcript);

      // Merge the rho transcript back into the global one to ensure its advanced, while
      // simultaneously committing to everything
      self
        .params
        .algorithm
        .transcript()
        .append_message(b"rho_transcript", rho_transcript.challenge(b"merge"));
    }

    #[allow(non_snake_case)]
    let Rs = B.nonces(&nonces);
```

**File:** crypto/frost/src/nonce.rs (L161-173)
```rust
  pub(crate) fn calculate_binding_factors<T: Clone + Transcript>(&mut self, transcript: &T) {
    for (l, binding) in &mut self.0 {
      let mut transcript = transcript.clone();
      transcript.append_message(b"participant", C::F::from(u64::from(u16::from(*l))).to_repr());
      // It *should* be perfectly fine to reuse a binding factor for multiple nonces
      // This generates a binding factor per nonce just to ensure it never comes up as a question
      binding.binding_factors = Some(
        (0 .. binding.commitments.nonces.len())
          .map(|_| C::hash_binding_factor(transcript.challenge(b"rho").as_ref()))
          .collect(),
      );
    }
  }
```

**File:** crypto/frost/src/nonce.rs (L194-210)
```rust
  pub(crate) fn nonces(&self, planned_nonces: &[Vec<C::G>]) -> Vec<Vec<C::G>> {
    let mut nonces = Vec::with_capacity(planned_nonces.len());
    for n in 0 .. planned_nonces.len() {
      nonces.push(Vec::with_capacity(planned_nonces[n].len()));
      for g in 0 .. planned_nonces[n].len() {
        #[allow(non_snake_case)]
        let mut D = C::G::identity();
        let mut statements = Vec::with_capacity(self.0.len());
        #[allow(non_snake_case)]
        for IndividualBinding { commitments, binding_factors } in self.0.values() {
          D += commitments.nonces[n].generators[g].0[0];
          statements
            .push((binding_factors.as_ref().unwrap()[n], commitments.nonces[n].generators[g].0[1]));
        }
        nonces[n].push(D + multiexp_vartime(&statements));
      }
    }
```
