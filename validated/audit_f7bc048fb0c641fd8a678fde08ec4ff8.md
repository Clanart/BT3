## No vulnerability found for this question.

**Analysis:**

The premise underlying this question—that `computeChallenge` could hash non-canonical commitment bytes that differ from their canonical re-encoding—does not hold in this codebase.

1. **`computeChallenge` matches the spec exactly.** Per the consensus spec's `compute_challenge`, the raw commitment bytes supplied by the caller are hashed directly (not a re-serialized/canonicalized form). `Context.ComputeBlobKZGProof` calls `computeChallenge(blob, blobCommitment)` using the exact input bytes `blobCommitment`, which is the correct, spec-conformant behavior: [1](#0-0) [2](#0-1) 

2. **There is no "non-canonical but accepted" byte encoding for a commitment.** `DeserializeKZGCommitment` calls `deserializeG1Point`, which delegates to gnark-crypto's `G1Affine.SetBytes`: [3](#0-2) 
This function performs compressed-point decompression, which requires the encoded x-coordinate field element to be strictly canonical (< the field modulus); any out-of-range or non-canonical byte pattern is rejected with an error rather than being accepted and silently reduced. This is confirmed by the test vectors showing commitments with invalid/malformed bytes being rejected (`output: null`), e.g. the `compute_blob_kzg_proof_case_invalid_commitment_*` vectors. There is exactly one accepted 48-byte encoding per valid G1 point—there is no second "accepted non-canonical" byte string that decodes to the same point, so the "two accepted encodings hash to two different challenges" scenario the question posits cannot be constructed.

3. **`computeChallenge` hashing raw bytes is intentional and spec-matching**, not a bug: since only one byte encoding is ever accepted for a given commitment point, "hashing raw bytes" and "hashing the canonical re-encoding" are the same operation, by construction. This is also verified by the interop regression test against the known consensus-spec challenge value: [4](#0-3) 

Because the commitment deserialization path (`SetBytes`) already enforces canonicity as part of subgroup/point validation, and because `computeChallenge` correctly hashes the exact bytes per spec, the claimed equality gap ("`computeChallenge` over supplied bytes" vs. "`computeChallenge` over canonical re-encoding") cannot diverge for any input that passes `ComputeBlobKZGProof`'s deserialization check.

### Citations

**File:** fiatshamir.go (L22-33)
```go
func computeChallenge(blob *Blob, commitment KZGCommitment) fr.Element {
	h := sha256.New()
	h.Write([]byte(DomSepProtocol))
	h.Write(u64ToByteArray16(ScalarsPerBlob))
	h.Write(blob[:])
	h.Write(commitment[:])

	digest := h.Sum(nil)
	var challenge fr.Element
	challenge.SetBytes(digest[:])
	return challenge
}
```

**File:** prove.go (L58-67)
```go
	// Deserialize commitment
	//
	// We only do this to check if it is in the correct subgroup
	_, err = DeserializeKZGCommitment(blobCommitment)
	if err != nil {
		return KZGProof{}, err
	}

	// 2. Compute Fiat-Shamir challenge
	evaluationChallenge := computeChallenge(blob, blobCommitment)
```

**File:** serialization.go (L109-123)
```go
func deserializeG1Point(serPoint G1Point) (bls12381.G1Affine, error) {
	var point bls12381.G1Affine
	_, err := point.SetBytes(serPoint[:])
	if err != nil {
		return bls12381.G1Affine{}, err
	}
	return point, nil
}

// DeserializeKZGCommitment implements [bytes_to_kzg_commitment].
//
// [bytes_to_kzg_commitment]: https://github.com/ethereum/consensus-specs/blob/017a8495f7671f5fff2075a9bfc9238c1a0982f8/specs/deneb/polynomial-commitments.md#bytes_to_kzg_commitment
func DeserializeKZGCommitment(commitment KZGCommitment) (bls12381.G1Affine, error) {
	return deserializeG1Point(G1Point(commitment))
}
```

**File:** fiatshamir_test.go (L14-26)
```go
func TestComputeChallengeInterop(t *testing.T) {
	blob := &Blob{}
	commitment := SerializeG1Point(bls12381.G1Affine{})
	challenge := computeChallenge(blob, KZGCommitment(commitment))
	expected := []byte{
		0x04, 0xb7, 0xb2, 0x2a, 0xf6, 0x3d, 0x2b, 0x2f,
		0x1c, 0xed, 0x8d, 0x55, 0x05, 0x60, 0xe5, 0xd1,
		0xe4, 0xb0, 0x1e, 0x35, 0x59, 0x03, 0xde, 0xe2,
		0x27, 0x81, 0xe8, 0x78, 0x26, 0x85, 0x60, 0x96,
	}
	got := SerializeScalar(challenge)
	require.Equal(t, expected, got[:])
}
```
