## No vulnerability found for this question.

**Trace summary:**

The claimed broken equality is: `getPolynomial() result with a warm pool == getPolynomial() result with a cold pool`, i.e. that a pooled `kzg.Polynomial` slice returned via `polynomialPool.Get()` could carry stale/leftover bytes from a previous call into a new `Context.VerifyBlobKZGProof` invocation.

Tracing `Context.VerifyBlobKZGProof` in `verify.go`: [1](#0-0) 

it calls `getPolynomial()` and immediately `deserializeBlobToPoly(blob, polynomial)`, which fully overwrites **every** element of the polynomial slice (indices `0..ScalarsPerBlob-1`) from the caller-supplied blob bytes via `poly[i].SetBytesCanonical(chunk)`: [2](#0-1) 

Since `polynomialPool.New` always allocates exactly `ScalarsPerBlob` (4096) elements and `getPolynomial`/`putPolynomial` never resize or slice the buffer, the pooled slice returned by `Get()` is always exactly 4096 elements, and `deserializeBlobToPoly` writes all 4096 positions unconditionally — there is no partial/fixed-size boundary read of stale bytes. The polynomial's content after `deserializeBlobToPoly` returns is a pure function of `blob`, regardless of what was in the pooled buffer beforehand (warm or cold pool). Any prior mutation of the pooled buffer (e.g., by `EvaluateLagrangePolynomial`'s in-place FFT-related operations from a previous call) is irrelevant because every element is rewritten before being read.

The `ComputeCells`/`BitReverse`/`IfftFr` code path cited in the question belongs to a different method (`Context.ComputeCells`/related recovery/proving paths in `prove.go`), not `VerifyBlobKZGProof`; `VerifyBlobKZGProof` only calls `deserializeBlobToPoly` and `c.domain.EvaluateLagrangePolynomial`, and `putPolynomial` is deferred until after the polynomial is no longer needed (`defer putPolynomial(polynomial)`), so there's no aliasing across concurrent calls within a single goroutine's use of the buffer.

Additionally, in the batch verification path (`VerifyBlobKZGProofBatch` in `verify.go`), the code explicitly guards against a related but distinct issue — `EvaluateLagrangePolynomial` potentially returning a pointer into the polynomial slice — by copying `claimedValue := *outputPoint` **before** calling `putPolynomial(polynomial)`: [3](#0-2) 

This confirms the maintainers were already aware of pool-aliasing hazards and addressed the one that actually exists (using a value after it's returned to the pool), while the specific scenario in this question (stale bytes surviving a fixed-size decode) does not occur because the decode step is a full, unconditional 4096-element overwrite.

No divergence between "warm pool" and "cold pool" results exists: the deserialization step is complete and deterministic for every call, satisfying the purity invariant.

### Citations

**File:** verify.go (L48-56)
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

**File:** serialization.go (L151-162)
```go
func deserializeBlobToPoly(blob *Blob, poly kzg.Polynomial) error {
	if blob == nil {
		return ErrDeserializeNilInput
	}
	for i := range ScalarsPerBlob {
		chunk := blob[i*SerializedScalarSize : (i+1)*SerializedScalarSize]
		if err := poly[i].SetBytesCanonical(chunk); err != nil {
			return ErrNonCanonicalScalar
		}
	}
	return nil
}
```
