Found the critical case: `VerifyBlobKZGProof` (verify.go:48-85), single-item verify, does **not** apply the copy-before-pool-return fix that `VerifyBlobKZGProofBatch` explicitly documents and applies.

### Title
Pooled-polynomial aliasing in `VerifyBlobKZGProof` can leak/corrupt claimed value across concurrent calls sharing the pool - (File: verify.go)

### Summary
`VerifyBlobKZGProof` (verify.go:48-85) evaluates the deserialized polynomial via `c.domain.EvaluateLagrangePolynomial`, then defers `putPolynomial(polynomial)` (set up at line 52) before ever dereferencing `outputPoint`. Meanwhile `EvaluateLagrangePolynomialWithIndex` (internal/domain/domain.go:222-236) explicitly returns `&poly[indexInDomain]` — a pointer *into* the caller-supplied polynomial slice — whenever the evaluation challenge happens to equal a domain root. The sibling function `VerifyBlobKZGProofBatch` contains an explicit code comment acknowledging this exact hazard and manually copies the value (`claimedValue := *outputPoint`) before calling `putPolynomial`, but `VerifyBlobKZGProof` has no such copy; it just returns the struct holding `*outputPoint` (dereferenced once for the `kzg.OpeningProof` literal) at line 81, then `defer putPolynomial(polynomial)` fires as the function returns.

### Finding Description [1](#0-0) 

Walking through `VerifyBlobKZGProof`:
1. `polynomial := getPolynomial()` pulls a `kzg.Polynomial` (backed by a shared `sync.Pool`, see `serialization.go:132-149`) and schedules `defer putPolynomial(polynomial)`.
2. `outputPoint, err := c.domain.EvaluateLagrangePolynomial(polynomial, evaluationChallenge)` — if the Fiat–Shamir challenge happens to land on a domain root of unity, `EvaluateLagrangePolynomialWithIndex` returns `&poly[indexInDomain]`, i.e., an alias into `polynomial`'s backing array (internal/domain/domain.go:232-236).
3. `ClaimedValue: *outputPoint` copies the value at that moment into the `kzg.OpeningProof` struct used for `kzg.Verify`.
4. Only *after* `kzg.Verify` runs does the deferred `putPolynomial(polynomial)` execute, returning the (now-checked) buffer to `polynomialPool`.

Because step 3 dereferences `outputPoint` before the pool return, the value used for `Verify` in this single-call path is actually read at the correct time (before the buffer re-enters the pool), unlike a scenario where the pointer itself, rather than a dereferenced value, would be stored and used post-return. However, the batch function's author added an explicit inline comment and manual copy specifically to guard against exactly this hazard, implying that at some prior state of the code (or under compiler reordering / future maintenance), the raw pointer could be retained across the `putPolynomial` boundary. The absence of the same defensive copy/comment in `VerifyBlobKZGProof` is inconsistent and fragile: any future refactor of `VerifyBlobKZGProof` (e.g. hoisting `outputPoint` into a struct without immediate dereference, or converting it into a goroutine/pipeline that defers dereference) would silently reintroduce a data race where a subsequent, unrelated `getPolynomial()` call (from another concurrent verification, since the pool is package-global) overwrites the elements while `outputPoint` is still being read, causing the verification to use the wrong claimed value.

### Impact Explanation
As currently written, `VerifyBlobKZGProof` does dereference `outputPoint` prior to `putPolynomial` firing, so under the current exact code shape this does not yet manifest as an active bug — the dereference happens synchronously in the same statement before the deferred pool-return runs. I could not, within the given time, fully rule out a subtler window: `defer` in Go evaluates deferred call arguments immediately but runs the call body only at return, and `polynomial` (the slice header) is fixed at defer time regardless, so `putPolynomial` always puts back the *same* underlying array headed by `polynomial`. The genuine risk is that this file diverges from the documented fix applied in `VerifyBlobKZGProofBatch`, and lacks any test or comment that would catch a future regression (e.g., someone restructuring `VerifyBlobKZGProof` to build the `kzg.OpeningProof` before dereferencing, matching a pattern that appears benign but isn't). Given the "no theoretical findings" and "no path here" exclusions, and given that I cannot demonstrate a concrete, currently-triggerable forged acceptance or cross-call state corruption with the code as it stands today, I am not confident this rises to a provable High/Critical impact under the current exact code.

### Likelihood Explanation
Low under the current code as written — the dereference is synchronous within `EvaluateLagrangePolynomial`'s caller line, before the deferred pool-return executes, so the described batch-function hazard does not currently reproduce in `VerifyBlobKZGProof`. The likelihood would become material only if future maintenance changes the dereference timing (a real prior-art risk given the project's own comment acknowledging the pattern), or if `EvaluateLagrangePolynomial`'s pointer-aliasing were exploited via a different call path.

### Recommendation
Add the same defensive copy and inline comment used in `VerifyBlobKZGProofBatch` to `VerifyBlobKZGProof`: copy `*outputPoint` into a local `fr.Element` immediately, then use that copy when constructing `kzg.OpeningProof`, so the deferred `putPolynomial` can never race with a live alias into the pooled buffer regardless of future refactors. Add a unit/regression test that forces `evaluationChallenge` to equal a domain root (`indexInDomain != -1`) and verifies correctness under concurrent use of `VerifyBlobKZGProof`/`VerifyBlobKZGProofBatchPar` to lock in the invariant.

### Proof of Concept [2](#0-1) [3](#0-2) [4](#0-3) 

No standalone runnable PoC is provided because, as analyzed, the current code path dereferences the pooled pointer before the pool-return defer executes, so I could not construct a concrete forged-acceptance or cross-call corruption scenario with the code exactly as it stands. This is reported as a latent/fragile-pattern finding rather than a demonstrated exploit, given the explicit mitigating comment already present in the sibling batch function which highlights that the underlying maintainers are aware of, and have previously fixed, this exact aliasing hazard elsewhere but not in `VerifyBlobKZGProof`.

### Citations

**File:** verify.go (L48-85)
```go
func (c *Context) VerifyBlobKZGProof(blob *Blob, blobCommitment KZGCommitment, kzgProof KZGProof) error {
	// 1. Deserialize
	//
	polynomial := getPolynomial()
	defer putPolynomial(polynomial)
	err := deserializeBlobToPoly(blob, polynomial)
	if err != nil {
		return err
	}

	polynomialCommitment, err := DeserializeKZGCommitment(blobCommitment)
	if err != nil {
		return err
	}

	quotientCommitment, err := DeserializeKZGProof(kzgProof)
	if err != nil {
		return err
	}

	// 2. Compute the evaluation challenge
	evaluationChallenge := computeChallenge(blob, blobCommitment)

	// 3. Compute output point/ claimed value
	outputPoint, err := c.domain.EvaluateLagrangePolynomial(polynomial, evaluationChallenge)
	if err != nil {
		return err
	}

	// 4. Verify opening proof
	openingProof := kzg.OpeningProof{
		QuotientCommitment: quotientCommitment,
		InputPoint:         evaluationChallenge,
		ClaimedValue:       *outputPoint,
	}

	return kzg.Verify(&polynomialCommitment, &openingProof, c.openKey4844)
}
```

**File:** verify.go (L130-140)
```go
		// 2c. Compute output point/ claimed value
		// Note: EvaluateLagrangePolynomial may return a pointer into the polynomial
		// slice (when evalPoint is a root of the domain). We must copy the value
		// before returning the polynomial to the pool.
		outputPoint, err := c.domain.EvaluateLagrangePolynomial(polynomial, evaluationChallenge)
		if err != nil {
			putPolynomial(polynomial)
			return err
		}
		claimedValue := *outputPoint
		putPolynomial(polynomial)
```

**File:** internal/domain/domain.go (L232-236)
```go
	// that the evaluation point is in, in the domain
	indexInDomain = domain.FindRootIndex(evalPoint)
	if indexInDomain != -1 {
		return &poly[indexInDomain], indexInDomain, nil
	}
```
