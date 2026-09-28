### Title
PedPoP evaluates degree-≥2 polynomials incorrectly, causing honest DKG sessions to abort - ([File: crypto/dkg/pedpop/src/lib.rs])

### Summary
`polynomial` applies the multiplication at the wrong stage of Horner evaluation, so every polynomial with `t >= 3` produces shares for a different polynomial than the one represented by the sender's commitments. [1](#0-0)  Recipients verify each decrypted share against the correct polynomial defined by `commitments[j] * i^j`, so honest shares are rejected and `calculate_share` returns `PedPoPError::InvalidShare`. [2](#0-1) [3](#0-2) 

### Finding Description
For coefficients `c0, c1, c2`, the implementation iterates from the highest coefficient to the lowest, but multiplies after adding each coefficient. [4](#0-3)  Consequently, `polynomial([c0, c1, c2], x)` returns `c0 + (c1 + c2)x`, instead of `c0 + c1x + c2x²`. [4](#0-3)  More generally, all coefficients from `c2` through `c_{t-1}` are collapsed into the `x^{t-2}` term rather than occupying their own degrees. [4](#0-3) 

The malformed value is encrypted and sent as the participant's share. [5](#0-4)  The recipient's verifier independently constructs the standard coefficients `1, x, x², ...` and requires `share * G` to equal the corresponding committed polynomial evaluation. [6](#0-5)  The batch-verification failure is returned as an invalid share and the key-generation machine aborts. [3](#0-2) 

### Impact Explanation
Any PedPoP instance with a threshold of at least three cannot complete when participants run this code, because every sender emits shares that fail their own committed verification equation. [7](#0-6) [4](#0-3)  This prevents creation of the `ThresholdKeys` used by Serai's threshold wallet and can repeatedly consume protocol rounds while incorrectly attributing blame to honest senders. [3](#0-2) [8](#0-7) 

### Likelihood Explanation
The condition is deterministic for the common `t >= 3` setting and does not require malformed peer input, collusion, leaked secrets, or implementation misuse. [9](#0-8) [4](#0-3)  Random nonzero coefficients make the erroneous and expected evaluations differ except for the negligible case where `c_{t-1}` happens to satisfy the accidental equality. [10](#0-9) 

### Recommendation
Rewrite `polynomial` as standard Horner evaluation: initialize the accumulator to the highest coefficient, then for each remaining coefficient in reverse order multiply the accumulator by the participant index and add that coefficient. [1](#0-0)  Add unit tests for thresholds above two that compare the scalar result directly against `sum(coefficients[j] * i^j)` and run an end-to-end PedPoP completion test for `t = 3`. [2](#0-1) [11](#0-10) 

### Proof of Concept
For `t = 3`, coefficients `[c0, c1, c2]`, and participant `x = 2`, the implementation evaluates:

```rust
// crypto/dkg/pedpop/src/lib.rs
// Iteration over [c2, c1, c0]:
share = c2;
share = (share + c1) * x;
share += c0;

// Therefore:
share == c0 + (c1 + c2) * 2
```

The verifier expects:

```rust
expected == c0 + c1 * 2 + c2 * 4
```

Thus `share - expected == -2 * c2`, which is nonzero whenever `c2 != 0`. [4](#0-3) [6](#0-5)  Since `c2` is generated as a random nonzero scalar, the recipient queues a non-identity verification statement and `calculate_share` returns `InvalidShare`. [10](#0-9) [3](#0-2)

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L165-184)
```rust
    for i in 0 .. t {
      // Step 1: Generate t random values to form a polynomial with
      coefficients.push(Zeroizing::new(C::random_nonzero_F(&mut *rng)));
      // Step 3: Generate public commitments
      commitments.push(C::generator() * coefficients[i].deref());
      cached_msg.extend(commitments[i].to_bytes().as_ref());
    }

    // Step 2: Provide a proof of knowledge
    let r = Zeroizing::new(C::random_nonzero_F(rng));
    let nonce = C::generator() * r.deref();
    let sig = SchnorrSignature::<C>::sign(
      &coefficients[0],
      // This could be deterministic as the PoK is a singleton never opened up to cooperative
      // discussion
      // There's no reason to spend the time and effort to make this deterministic besides a
      // general obsession with canonicity and determinism though
      r,
      challenge::<C>(self.context, self.params.i(), nonce.to_bytes().as_ref(), &cached_msg),
    );
```

**File:** crypto/dkg/pedpop/src/lib.rs (L205-219)
```rust
fn polynomial<F: PrimeField + Zeroize>(
  coefficients: &[Zeroizing<F>],
  l: Participant,
) -> Zeroizing<F> {
  let l = F::from(u64::from(u16::from(l)));
  // This should never be reached since Participant is explicitly non-zero
  assert!(l != F::ZERO, "zero participant passed to polynomial");
  let mut share = Zeroizing::new(F::ZERO);
  for (idx, coefficient) in coefficients.iter().rev().enumerate() {
    *share += coefficient.deref();
    if idx != (coefficients.len() - 1) {
      *share *= l;
    }
  }
  share
```

**File:** crypto/dkg/pedpop/src/lib.rs (L366-370)
```rust
      let mut share = polynomial(&self.coefficients, l);
      let share_bytes = Zeroizing::new(SecretShare::<C::F>(share.to_repr()));
      share.zeroize();
      res.insert(l, self.encryption.encrypt(rng, l, share_bytes));
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L420-449)
```rust
fn exponential<C: Ciphersuite>(i: Participant, values: &[C::G]) -> Vec<(C::F, C::G)> {
  let i = C::F::from(u16::from(i).into());
  let mut res = Vec::with_capacity(values.len());
  (0 .. values.len()).fold(C::F::ONE, |exp, l| {
    res.push((exp, values[l]));
    exp * i
  });
  res
}

fn share_verification_statements<C: Ciphersuite>(
  target: Participant,
  commitments: &[C::G],
  mut share: Zeroizing<C::F>,
) -> Vec<(C::F, C::G)> {
  // This can be insecurely linearized from n * t to just n using the below sums for a given
  // stripe. Doing so uses naive addition which is subject to malleability. The only way to
  // ensure that malleability isn't present is to use this n * t algorithm, which runs
  // per sender and not as an aggregate of all senders, which also enables blame
  let mut values = exponential::<C>(target, commitments);

  // Perform the share multiplication outside of the multiexp to minimize stack copying
  // While the multiexp BatchVerifier does zeroize its flattened multiexp, and itself, it still
  // converts whatever we give to an iterator and then builds a Vec internally, welcoming copies
  let neg_share_pub = C::generator() * -*share;
  share.zeroize();
  values.push((C::F::ONE, neg_share_pub));

  values
}
```

**File:** crypto/dkg/pedpop/src/lib.rs (L487-499)
```rust
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
      );
    }
    batch.verify_with_vartime_blame().map_err(|id| {
      let (l, blame) = match id {
        BatchId::Decryption(l) => (l, None),
        BatchId::Share(l) => (l, Some(blames.remove(&l).unwrap())),
      };
      PedPoPError::InvalidShare { participant: l, blame }
    })?;
```

**File:** crypto/dkg/pedpop/src/lib.rs (L523-531)
```rust
    let KeyMachine { commitments, encryption, params, secret } = self;
    Ok(BlameMachine {
      commitments,
      encryption: encryption.into_decryption(),
      result: Some(
        ThresholdKeys::new(params, Interpolation::Lagrange, secret, verification_shares)
          .map_err(PedPoPError::DkgError)?,
      ),
    })
```

**File:** crypto/dkg/pedpop/src/tests.rs (L10-12)
```rust
const THRESHOLD: u16 = 3;
const PARTICIPANTS: u16 = 5;

```
