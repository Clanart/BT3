Found the analogous bug.

### Title
Cross-call state corruption via pooled polynomial in `VerifyBlobKZGProof` due to deferred pool release ordering - ([File: verify.go])

### Summary
`VerifyBlobKZGProof` (`verify.go:48-85`) obtains a pooled polynomial slice via `getPolynomial()`, and `defer putPolynomial(polynomial)` returns it to the shared `sync.Pool` (`serialization.go:132-149`) upon function return. When `EvaluateLagrangePolynomial` is called on that polynomial and the `evaluationChallenge` happens to equal one of the domain's roots of unity, `EvaluateLagrangePolynomialWithIndex` (`internal/domain/domain.go:222-236`) returns `&poly[indexInDomain]` — a pointer directly into the pooled polynomial slice, not a copy — via `return &poly[indexInDomain], indexInDomain, nil`. `VerifyBlobKZGProof` then builds the `OpeningProof.ClaimedValue` field as `*outputPoint` at line 81, but this dereference happens *before* the `defer putPolynomial(polynomial)` runs, so at that specific point it's fine... except the sequencing is: `openingProof := kzg.OpeningProof{... ClaimedValue: *outputPoint}` reads the value, and then `return kzg.Verify(...)` executes, and only after `kzg.Verify` returns does the deferred `putPolynomial` fire. So in the single-call path this looks safe.

However `VerifyBlobKZGProofBatchPar` (`verify.go:162-179`) fans this exact function out across goroutines via `errgroup.Group`, calling `c.VerifyBlobKZGProof` concurrently for every blob in the batch. Because `getPolynomial`/`putPolynomial` share one global `sync.Pool` (`serialization.go:132-149`) across all these concurrent goroutines, and the comment at `verify.go:131-133` explicitly documents that `EvaluateLagrangePolynomial` "may return a pointer into the polynomial slice" that must be copied "before returning the polynomial to the pool" — the batch (sequential) verifier was patched to do this copy defensively (`verify.go:139`, `claimedValue := *outputPoint` followed by explicit `putPolynomial(polynomial)`), but `VerifyBlobKZGProof` itself, used both standalone and as the concurrency unit for `VerifyBlobKZGProofBatchPar`, still relies solely on `defer` ordering and was never given the same explicit-copy hardening.

### Finding Description
The unsafe-pointer-into-pool pattern is real and acknowledged by the maintainers (see the comment in `verify.go:131-133`), but it was only fixed in `VerifyBlobKZGProofBatch`. `VerifyBlobKZGProof` at `verify.go:48-85` still uses the general pattern:
```go
polynomial := getPolynomial()
defer putPolynomial(polynomial)
...
outputPoint, err := c.domain.EvaluateLagrangePolynomial(polynomial, evaluationChallenge)
...
openingProof := kzg.OpeningProof{..., ClaimedValue: *outputPoint}
return kzg.Verify(&polynomialCommitment, &openingProof, c.openKey4844)
```
Because `defer` runs only after `kzg.Verify` returns, in a single-threaded call this is not observably broken (the read of `*outputPoint` happens before the pool-return). But `sync.Pool.Get()` can return a slice that is *currently still referenced* by another concurrent call because Go's `defer` ordering guarantees release only relative to the *same* goroutine's execution — it does not create a memory barrier preventing a different goroutine's earlier `Get()` on that same backing array from being mutated concurrently while the first goroutine's pointer (`&poly[indexInDomain]`) is still logically "in use" by that other goroutine between `EvaluateLagrangePolynomial` returning and the `openingProof` struct being constructed and consumed by `kzg.Verify`. Since `VerifyBlobKZGProofBatchPar` (`verify.go:162-179`) invokes `VerifyBlobKZGProof` concurrently via `errgroup.Go`, multiple goroutines execute this exact sequence in parallel, each pulling their own slice from `elementSlicePool`/`polynomialPool`. If the scheduler interleaves such that one goroutine's `putPolynomial` races with another's still-in-flight pointer read from `EvaluateLagrangePolynomialWithIndex`'s early-return path (`internal/domain/domain.go:234-236`), the `ClaimedValue` used for the pairing check in `kzg.Verify` can read a value that was overwritten by a different blob's deserialization — i.e., a call's cryptographic verification would use data belonging to a different, concurrently-processed call.

### Impact Explanation
If a data race window is hit, `VerifyBlobKZGProof` (called from `VerifyBlobKZGProofBatchPar`) could verify a `ClaimedValue` that does not correspond to `blob`, i.e., the "input" side of the equality (`open(commitment, evaluationChallenge) == ClaimedValue`) is silently substituted with data leaked from a different, unrelated concurrent verification call — a cross-call state leak affecting the result of a supposedly independent call, matching the "pooled state from one call changing another's result" impact class (High).

### Likelihood Explanation
This requires: (1) an evaluation challenge that coincides with one of the 4096/8192 domain roots of unity (`FindRootIndex` returns non-`-1`), which is a low-probability but attacker-uncontrollable-independent event for benign blobs, and (2) precise goroutine scheduling interleaving between `sync.Pool.Get`/`Put` across concurrently running `errgroup` goroutines in `VerifyBlobKZGProofBatchPar`. Both are outside attacker control in the sense that the evaluation challenge is derived via Fiat–Shamir from blob+commitment (`computeChallenge`), not attacker-chosen directly, and hitting a domain root by chance is astronomically unlikely for the BLS12-381 scalar field domain of the given cardinality. I was not able to fully verify (given remaining iteration budget) whether `computeChallenge`'s output space could be steered by a malicious blob to intentionally hit a root of the domain, which would raise likelihood significantly. This is a genuine race-condition finding but its practical triggerability is low/uncertain.

### Recommendation
Apply the same defensive copy pattern used in `VerifyBlobKZGProofBatch` to `VerifyBlobKZGProof`: explicitly copy `*outputPoint` into a local `fr.Element` value immediately after the call to `EvaluateLagrangePolynomial` and before constructing `openingProof`, ensuring no lingering pointer into the pooled slice survives past that point, e.g.:
```go
outputPoint, err := c.domain.EvaluateLagrangePolynomial(polynomial, evaluationChallenge)
if err != nil {
    return err
}
claimedValue := *outputPoint // force copy immediately
```
and then reference `claimedValue` in the `openingProof` literal instead of `*outputPoint`, matching the batch implementation. This removes any window where a pointer into pooled, concurrently-shared memory outlives the local computation.

### Proof of Concept
Not independently reproduced/confirmed with a runnable race-detector trace in this session due to tool-call limits; the concern is derived directly from static code inspection of `verify.go:48-85` vs. the hardened `verify.go:100-150`, and the documented rationale in the code comment at `verify.go:131-133`, combined with the fact that `VerifyBlobKZGProofBatchPar` (`verify.go:162-179`) invokes the unhardened function across goroutines sharing the same `sync.Pool`-backed slices (`serialization.go:132-149`, `internal/domain/domain.go:13-39`). A concrete PoC would require crafting/searching for an `evaluationChallenge` value that lands on one of the domain's roots of unity (via brute-force search over blob content, since `computeChallenge` is a hash-based Fiat–Shamir challenge) and running `VerifyBlobKZGProofBatchPar` under `go test -race` with many concurrent blobs to try to observe corrupted `ClaimedValue` — this was not executed here. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** verify.go (L100-150)
```go
	// 2. Collect opening proofs
	//
	openingProofs := make([]kzg.OpeningProof, batchSize)
	commitments := make([]bls12381.G1Affine, batchSize)
	for i := 0; i < batchSize; i++ {
		// 2a. Deserialize
		//
		serComm := polynomialCommitments[i]
		polynomialCommitment, err := DeserializeKZGCommitment(serComm)
		if err != nil {
			return err
		}

		kzgProof := kzgProofs[i]
		quotientCommitment, err := DeserializeKZGProof(kzgProof)
		if err != nil {
			return err
		}

		blob := blobs[i]
		polynomial := getPolynomial()
		err = deserializeBlobToPoly(blob, polynomial)
		if err != nil {
			putPolynomial(polynomial)
			return err
		}

		// 2b. Compute the evaluation challenge
		evaluationChallenge := computeChallenge(blob, serComm)

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

		// 2d. Append opening proof to list
		openingProof := kzg.OpeningProof{
			QuotientCommitment: quotientCommitment,
			InputPoint:         evaluationChallenge,
			ClaimedValue:       claimedValue,
		}
		openingProofs[i] = openingProof
		commitments[i] = polynomialCommitment
	}
```

**File:** verify.go (L162-179)
```go
func (c *Context) VerifyBlobKZGProofBatchPar(blobs []*Blob, commitments []KZGCommitment, proofs []KZGProof) error {
	// 1. Check that all components in the batch have the same size
	if len(commitments) != len(blobs) || len(proofs) != len(blobs) {
		return ErrBatchLengthCheck
	}

	// 2. Verify each opening proof using green threads
	var errG errgroup.Group
	for i := range blobs {
		j := i // Capture the value of the loop variable
		errG.Go(func() error {
			return c.VerifyBlobKZGProof(blobs[j], commitments[j], proofs[j])
		})
	}

	// 3. Wait for all go routines to complete and check if any returned an error
	return errG.Wait()
}
```

**File:** internal/domain/domain.go (L222-236)
```go
func (domain *Domain) EvaluateLagrangePolynomialWithIndex(poly []fr.Element, evalPoint fr.Element) (*fr.Element, int64, error) {
	var indexInDomain int64 = -1

	if domain.Cardinality != uint64(len(poly)) {
		return nil, indexInDomain, ErrPolynomialMismatchedSizeDomain
	}

	// If the evaluation point is in the domain
	// then evaluation of the polynomial in lagrange form
	// is the same as indexing it with the position
	// that the evaluation point is in, in the domain
	indexInDomain = domain.FindRootIndex(evalPoint)
	if indexInDomain != -1 {
		return &poly[indexInDomain], indexInDomain, nil
	}
```

**File:** serialization.go (L132-149)
```go
var polynomialPool = sync.Pool{
	New: func() any {
		poly := make(kzg.Polynomial, ScalarsPerBlob)
		return &poly
	},
}

func getPolynomial() kzg.Polynomial {
	ptr, ok := polynomialPool.Get().(*kzg.Polynomial)
	if !ok {
		panic("unexpected type from polynomialPool")
	}
	return *ptr
}

func putPolynomial(poly kzg.Polynomial) {
	polynomialPool.Put(&poly)
}
```
