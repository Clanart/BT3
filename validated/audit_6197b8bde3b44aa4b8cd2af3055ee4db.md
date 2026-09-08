## No vulnerability found for this question.

**Analysis:**

The claim that `deserializeG1Point` allows torsion/small-subgroup points to pass is not supported by the code. `deserializeG1Point` calls `point.SetBytes(serPoint[:])` [1](#0-0) , and its docstring explicitly states it implements `validate_kzg_g1` and "will return an error if the point is not on the group **or if the point is not in the correct subgroup**" [2](#0-1) .

The codebase itself demonstrates awareness of the difference between default (subgroup-checked) `SetBytes` and explicitly bypassed checks: in `trusted_setup.go`, points from the trusted setup are decoded with `bls12381.NoSubgroupChecks()` specifically because that is a deliberate, documented bypass reserved for trusted, pre-vetted inputs: "This function performs no (expensive) subgroup checks, and should only be used for trusted inputs" [3](#0-2) . This confirms that the ordinary (default) `SetBytes` path used by `deserializeG1Point` — which does not pass any `NoSubgroupChecks` decoder option — performs the subgroup check by default.

`Context.VerifyKZGProof` deserializes `blobCommitment` and `kzgProof` via `DeserializeKZGCommitment`/`DeserializeKZGProof`, both of which route through `deserializeG1Point` [4](#0-3) [5](#0-4) . Since this path uses the subgroup-checking `SetBytes`, any on-curve-but-not-in-prime-order-subgroup (torsion) point encoding will fail deserialization and return an error before ever reaching the pairing check, batch or otherwise.

There is also a `TestG1RoundTripSmoke` test confirming the intended round-trip invariant for a valid subgroup element (`Serialize(Deserialize(bytes)) == bytes`) [6](#0-5) , and no code path exists where `deserializeG1Point` is called with subgroup checks disabled outside of the trusted-setup-only parsing functions, which are not reachable from `VerifyKZGProof` or any other attacker-facing method.

Since the premise — that the subgroup check is skipped or delegated incorrectly in `deserializeG1Point` — does not hold, the batch/per-member divergence and the `Serialize(Deserialize(bytes)) != bytes` witness described in the question cannot be constructed through this path.

### Citations

**File:** serialization.go (L103-108)
```go
// deserializeG1Point converts a [G1Point] to the internal [bls12381.G1Affine] type. It will return an error if the
// point is not on the group or if the point is not in the correct subgroup.
//
// It implements [validate_kzg_g1].
//
// [validate_kzg_g1]: https://github.com/ethereum/consensus-specs/blob/017a8495f7671f5fff2075a9bfc9238c1a0982f8/specs/deneb/polynomial-commitments.md#validate_kzg_g1
```

**File:** serialization.go (L109-116)
```go
func deserializeG1Point(serPoint G1Point) (bls12381.G1Affine, error) {
	var point bls12381.G1Affine
	_, err := point.SetBytes(serPoint[:])
	if err != nil {
		return bls12381.G1Affine{}, err
	}
	return point, nil
}
```

**File:** serialization.go (L118-130)
```go
// DeserializeKZGCommitment implements [bytes_to_kzg_commitment].
//
// [bytes_to_kzg_commitment]: https://github.com/ethereum/consensus-specs/blob/017a8495f7671f5fff2075a9bfc9238c1a0982f8/specs/deneb/polynomial-commitments.md#bytes_to_kzg_commitment
func DeserializeKZGCommitment(commitment KZGCommitment) (bls12381.G1Affine, error) {
	return deserializeG1Point(G1Point(commitment))
}

// DeserializeKZGProof implements [bytes_to_kzg_proof].
//
// [bytes_to_kzg_proof]: https://github.com/ethereum/consensus-specs/blob/017a8495f7671f5fff2075a9bfc9238c1a0982f8/specs/deneb/polynomial-commitments.md#bytes_to_kzg_proof
func DeserializeKZGProof(proof KZGProof) (bls12381.G1Affine, error) {
	return deserializeG1Point(G1Point(proof))
}
```

**File:** trusted_setup.go (L102-117)
```go
// parseG1PointNoSubgroupCheck parses a hex-string (with the 0x prefix) into a G1 point.
//
// This function performs no (expensive) subgroup checks, and should only be used
// for trusted inputs.
func parseG1PointNoSubgroupCheck(hexString string) (bls12381.G1Affine, error) {
	byts, err := hex.DecodeString(trim0xPrefix(hexString))
	if err != nil {
		return bls12381.G1Affine{}, err
	}

	var point bls12381.G1Affine
	noSubgroupCheck := bls12381.NoSubgroupChecks()
	d := bls12381.NewDecoder(bytes.NewReader(byts), noSubgroupCheck)

	return point, d.Decode(&point)
}
```

**File:** verify.go (L12-43)
```go
func (c *Context) VerifyKZGProof(blobCommitment KZGCommitment, inputPointBytes, claimedValueBytes Scalar, kzgProof KZGProof) error {
	// 1. Deserialization
	//
	claimedValue, err := DeserializeScalar(claimedValueBytes)
	if err != nil {
		return err
	}

	inputPoint, err := DeserializeScalar(inputPointBytes)
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

	// 2. Verify opening proof
	proof := kzg.OpeningProof{
		QuotientCommitment: quotientCommitment,
		InputPoint:         inputPoint,
		ClaimedValue:       claimedValue,
	}

	return kzg.Verify(&polynomialCommitment, &proof, c.openKey4844)
}
```

**File:** serialization_test.go (L14-24)
```go
func TestG1RoundTripSmoke(t *testing.T) {
	_, _, g1Aff, _ := bls12381.Generators()
	g1Bytes := goethkzg.SerializeG1Point(g1Aff)
	aff, err := goethkzg.DeserializeKZGProof(goethkzg.KZGProof(g1Bytes))
	if err != nil {
		t.Error(err)
	}
	if !aff.Equal(&g1Aff) {
		t.Error("G1 serialization roundtrip fail")
	}
}
```
