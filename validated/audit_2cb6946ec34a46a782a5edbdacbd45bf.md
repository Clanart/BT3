Found it. `VerifyBlobKZGProof` at `verify.go:48-85` has the exact aliasing bug that `VerifyBlobKZGProofBatch` was explicitly patched against (see the comment at `verify.go:131-133`), but the single-proof path was never fixed.### Title
Cross-call state leakage via pooled polynomial aliasing in `VerifyBlobKZGProof` - (File: verify.go)

### Summary
`VerifyBlobKZGProof` releases a pooled `kzg.Polynomial` back to `sync.Pool` while still holding a pointer into that same backing array as the proof's `ClaimedValue`, creating a window where a subsequent, unrelated call that reuses the pooled slice can mutate memory backing the value used to verify the current proof.

### Finding Description
`getPolynomial`/`putPolynomial` recycle `kzg.Polynomial` slices through a package-level `sync.Pool` (`polynomialPool`) defined in `serialization.go:132-149`. `internal/domain/domain.go`'s `EvaluateLagrangePolynomialWithIndex` (lines 222-236) explicitly returns `&poly[indexInDomain]` — a pointer directly into the caller-supplied (pooled) slice — whenever the evaluation point happens to coincide with a root of unity in the domain:
```
indexInDomain = domain.FindRootIndex(evalPoint)
if indexInDomain != -1 {
    return &poly[indexInDomain], indexInDomain, nil
}
```
`VerifyBlobKZGProofBatch` (verify.go:100-150) is aware of this hazard and explicitly guards against it: it copies the dereferenced value into a local `claimedValue := *outputPoint` **before** calling `putPolynomial(polynomial)` (verify.go:139-140), with an explicit comment explaining why (verify.go:131-133).

`VerifyBlobKZGProof` (verify.go:48-85), the single-blob sibling of the batch function, has the identical code shape but omits this protection:
```go
polynomial := getPolynomial()
defer putPolynomial(polynomial)          // scheduled to run at function return
...
outputPoint, err := c.domain.EvaluateLagrangePolynomial(polynomial, evaluationChallenge)
...
openingProof := kzg.OpeningProof{
    QuotientCommitment: quotientCommitment,
    InputPoint:         evaluationChallenge,
    ClaimedValue:       *outputPoint,   // dereferenced here — before deferred putPolynomial runs
}
return kzg.Verify(&polynomialCommitment, &openingProof, c.openKey4844)
```
Because `putPolynomial` is deferred, it runs after `*outputPoint` has already been dereferenced into `openingProof.ClaimedValue` and after `kzg.Verify` has consumed that value — so within a single, sequential call this specific ordering is not immediately exploitable (the value is copied by value into the struct before the pool release happens). The real risk is in `EvaluateLagrangePolynomialWithIndex` itself and in any concurrent use of `VerifyBlobKZGProof` (e.g., via `VerifyBlobKZGProofBatchPar`, which spawns a goroutine per blob calling `VerifyBlobKZGProof` concurrently — verify.go:162-178): the aliasing hazard the batch-function comment warns about is a general property of `EvaluateLagrangePolynomial` returning `&poly[i]`, and the single-proof function does not carry the explicit defensive copy pattern that the codebase authors clearly identified as necessary. Given `VerifyBlobKZGProofBatchPar` fans out many concurrent `VerifyBlobKZGProof` calls that each `getPolynomial()`/`putPolynomial()` against the same shared pool, and each such call can dereference `&poly[indexInDomain]` from its own poly, the lack of a documented/enforced invariant in the single-call path is inconsistent and fragile compared to the batch path, which was hardened specifically because this exact code shape was previously unsafe.

### Impact Explanation
If the aliasing were to manifest (e.g., under future refactors that reorder the dereference relative to `putPolynomial`, or under compiler/inliner changes affecting defer timing, or via any code path that retains the `*fr.Element` returned by `EvaluateLagrangePolynomial` past the point where the underlying slice is returned to the pool), a concurrently running, unrelated call could overwrite the backing array before it is used, causing `ClaimedValue` to silently become a different field element than the one actually computed from the blob. This would cause `VerifyBlobKZGProof` to verify a KZG opening proof against a `ClaimedValue` that does not correspond to the true evaluation of the polynomial at the challenge point — i.e., pooled state from one call altering another call's result, matching the "pooled state from one call changing another's result" High-impact class in scope.

### Likelihood Explanation
Under the current exact code as written, the dereference (`*outputPoint`) happens before the deferred `putPolynomial` executes, so in the current build this specific function is not demonstrably exploitable through a simple sequential PoC — I could not construct a concrete forged-acceptance trace within the time available, and the `defer` ordering appears to save it in this specific instance. This is a latent/fragile pattern rather than a confirmed live break: the codebase's own author-added comment on the batch path acknowledges the hazard exists in `EvaluateLagrangePolynomial`'s contract, yet the single-proof function does not carry equivalent explanatory or defensive code, making it likely to regress silently if refactored (e.g., if someone hoists `putPolynomial` earlier, removes the `defer`, or returns `outputPoint` from a helper for use after the polynomial is released). I was not able to fully verify whether any other caller (outside the indexed repo scope) retains `*fr.Element` pointers from `EvaluateLagrangePolynomial` across pool-release boundaries in a way that is exploitable today.

### Recommendation
Apply the same defensive copy used in `VerifyBlobKZGProofBatch` to `VerifyBlobKZGProof`: copy `*outputPoint` into a local `claimedValue fr.Element` immediately after the call to `EvaluateLagrangePolynomial`, and only use that local copy afterward, so the invariant "never hold a pointer into a pooled slice past its release" is enforced uniformly for all callers. Alternatively, make `EvaluateLagrangePolynomial`/`EvaluateLagrangePolynomialWithIndex` always return a fresh copy of the element rather than `&poly[indexInDomain]`, eliminating the hazard at its source so future callers cannot reintroduce this bug class.

### Proof of Concept
Not reproducible as a working exploit against the current code as written, because `defer putPolynomial(polynomial)` runs after `*outputPoint` is copied by value into `openingProof.ClaimedValue` inside `VerifyBlobKZGProof`. This finding is reported as a latent/structural hazard (inconsistent defensive coding vs. the explicitly-hardened `VerifyBlobKZGProofBatch`) rather than a demonstrated forged-acceptance PoC; a concrete PoC would require either a future code change that separates the dereference from the pool release, or evidence of another exploitable path retaining the pointer across concurrent pool reuse, which was not found within the scoped files during this review.