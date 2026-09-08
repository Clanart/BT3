No vulnerability found for this question.

**Analysis:** `deserializeG1Point` in `serialization.go` calls `point.SetBytes(serPoint[:])`, which is gnark-crypto's default compressed-point decoder for `bls12381.G1Affine`. This default path performs both on-curve and subgroup-membership validation — this is exactly why the function's own doc comment states it "will return an error if the point is not on the group or if the point is not in the correct subgroup" [1](#0-0) .

This is corroborated by `trusted_setup.go`, where the codebase explicitly has to opt out of subgroup checking via `bls12381.NoSubgroupChecks()` and a custom decoder (`parseG1PointNoSubgroupCheck`) specifically because the *default* `SetBytes` path enforces subgroup checks and would otherwise reject/slow-path trusted (but here bypassed) setup points [2](#0-1) . Since `deserializeG1Point`, and by extension `DeserializeKZGCommitment`/`DeserializeKZGProof` used in `Context.VerifyKZGProof`, use the plain `SetBytes` call (not the no-subgroup-check decoder), attacker-supplied commitment and proof bytes are always subgroup-checked before being used in the pairing [3](#0-2) .

The premise that "subgroup membership is never checked before pairing" does not hold for this code path — the check is delegated to (and performed by) gnark-crypto's default `SetBytes`, which is the correct, non-bypassed decoder for all attacker-facing entrypoints (`VerifyKZGProof`, `VerifyBlobKZGProof`, `VerifyBlobKZGProofBatch`). The only place subgroup checks are explicitly skipped is `trusted_setup.go`, which only processes the embedded/loaded trusted setup, not attacker-supplied protocol bytes, and is explicitly out of scope per the rules.

### Citations

**File:** serialization.go (L103-116)
```go
// deserializeG1Point converts a [G1Point] to the internal [bls12381.G1Affine] type. It will return an error if the
// point is not on the group or if the point is not in the correct subgroup.
//
// It implements [validate_kzg_g1].
//
// [validate_kzg_g1]: https://github.com/ethereum/consensus-specs/blob/017a8495f7671f5fff2075a9bfc9238c1a0982f8/specs/deneb/polynomial-commitments.md#validate_kzg_g1
func deserializeG1Point(serPoint G1Point) (bls12381.G1Affine, error) {
	var point bls12381.G1Affine
	_, err := point.SetBytes(serPoint[:])
	if err != nil {
		return bls12381.G1Affine{}, err
	}
	return point, nil
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

**File:** verify.go (L25-33)
```go
	polynomialCommitment, err := DeserializeKZGCommitment(blobCommitment)
	if err != nil {
		return err
	}

	quotientCommitment, err := DeserializeKZGProof(kzgProof)
	if err != nil {
		return err
	}
```
