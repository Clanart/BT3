### Title
`RecoverPolynomialCoefficients` truncates recovered coefficients without verifying high-order terms are zero, breaking the `recovered[cellIDs[i]] == cells[i]` invariant for over-supplied/inconsistent cell sets - (File: `internal/erasure_code/erasure_code.go`)

### Summary
`Context.RecoverCellsAndComputeKZGProofs` (`api_eip7594.go:148-165`) accepts any `cellIDs`/`cells` set with `len(cellIDs) >= NumBlocksNeededToReconstruct()` and no upper bound, then feeds it into `RecoverPolynomialCoefficients` (`internal/erasure_code/erasure_code.go:110-148`), which blindly truncates the full `numScalarsInCodeword`-length recovered coefficient vector to the first `numScalarsInDataWord` (4096) entries without ever checking that the discarded coefficients (indices ≥ 4096) are zero. When more than the strict minimum number of cells is supplied with values that are not all evaluations of one common degree < 4096 polynomial, the untruncated recovery polynomial can carry a nonzero coefficient at index ≥ 4096, and discarding it changes the polynomial's evaluations - meaning the truncated result no longer satisfies `recovered[cellIDs[i]] == cells[i]` for the very cells the caller supplied.

### Finding Description
The broken equality is: `the recovered polynomial's degree < ScalarsPerBlob (4096)` vs. `recovered[cellIDs[i]] == cells[i]` for all supplied indices. The code only guarantees the latter for the *untruncated* polynomial `Q` computed at `internal/erasure_code/erasure_code.go:110-144` (this is an exact algebraic identity: `D = Q*Z` pointwise wherever `Z != 0`, i.e. at all supplied cell positions, regardless of whether the input is "consistent" data).

The degree bound (`Q` has zero coefficients at indices ≥ 4096) is only guaranteed when *exactly* `NumBlocksNeededToReconstruct()` cells are supplied - in that case Lagrange interpolation trivially forces a unique degree < 4096 polynomial through exactly 4096 known points, so nothing is lost when the code truncates: [1](#0-0) 

However, `recoverPolynomialCoeffs` (`api_eip7594.go:97-146`) imposes only a lower bound on the number of supplied cells, not an upper bound or an exact-match requirement: [2](#0-1) 

If the attacker supplies more than the minimum number of cells (i.e., `missingCellIds` ends up shorter than the maximum), the vanishing polynomial `Z` constructed by `constructVanishingPolyOnIndices` has degree less than 4096, and the known-value set becomes over-determined relative to the degree-4096 assumption. If the attacker's supplied cell values are not all evaluations of one common degree < 4096 polynomial (fully within their control, since they author raw `Cell` bytes with no accompanying commitment/proof check inside this API), the full (untruncated) `cosetQuotientEval` can carry a nonzero coefficient at index ≥ 4096. `RecoverPolynomialCoefficients` performs no check on this and truncates anyway: [3](#0-2) 

Truncating discards that nonzero term, producing a different, lower-degree polynomial whose evaluations at the originally-known cell positions generally no longer equal the values the attacker actually supplied. `computeCellsFromPolyCoeff`/`computeKZGProofsFromPolyCoeff` then happily emit cells and proofs derived from this altered polynomial with no re-check against the caller-supplied cells: [4](#0-3) 

No existing guard prevents this: `isAscending`, the `cellID >= CellsPerExtBlob` check, and `len(cellIDs) < NumBlocksNeededToReconstruct()` (`api_eip7594.go:103-117`) only bound minimum count and validity of IDs; none of them enforce an upper bound on supplied cells or verify that the recovered polynomial's high-order coefficients are zero before truncation.

### Impact Explanation
This function has no commitment/proof parameter, so it does not itself verify that the input cells are genuine evaluations of any real KZG-committed polynomial - it is a pure Reed-Solomon reconstruction utility exposed directly as a public method. An unprivileged caller can invoke it with self-authored, mutually-inconsistent-but-length-valid cell data (more cells than the minimum threshold, not all consistent with one degree<4096 polynomial) and receive silently-wrong "recovered" cells/proofs that do not actually reproduce the values the caller supplied at the known positions, with no error returned. This is a self-consistency violation of the function's documented contract (`for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i]`), and it is fully repeatable per call with attacker-chosen inputs.

### Likelihood Explanation
Preconditions: attacker must supply strictly more than `ctx.dataRecovery.NumBlocksNeededToReconstruct()` (64) cells, with at least some of them chosen so that the full set is not consistent with a single degree < 4096 polynomial - entirely under attacker control and free of cost since it's a pure local computation with no external dependency (no real blob/commitment needed). Feasibility of constructing a concrete cell set that pushes a nonzero coefficient specifically to index ≥ 4096 (rather than causing an outright reconstruction failure elsewhere) requires careful polynomial construction but is not blocked by any code-level guard.

### Recommendation
After computing the full `cosetQuotientEval` in `RecoverPolynomialCoefficients`, explicitly verify that all coefficients at indices `>= numScalarsInDataWord` are zero before truncating, and return an error (e.g. a new `ErrPolynomialDegreeTooHigh`) if not. Additionally, consider enforcing that `recoverPolynomialCoeffs` in `api_eip7594.go` receives exactly `NumBlocksNeededToReconstruct()` cells (or validating full consistency for any larger supplied set) rather than only a lower bound.

### Proof of Concept
```go
// In package erasure_code (or via ctx.RecoverCellsAndComputeKZGProofs):
// 1. Construct extendedBlob/data such that more than NumBlocksNeededToReconstruct()
//    "known" blocks are supplied, with block values NOT consistent with a single
//    degree<4096 polynomial (e.g. take a real low-degree polynomial's evaluations
//    for most known blocks, then flip one scalar in an extra known block).
// 2. Call dr.RecoverPolynomialCoefficients(data, missingIndices) directly.
// 3. Assert polyCoeff has length numScalarsInDataWord (4096) - degree < ScalarsPerBlob holds by construction of the slice.
// 4. Re-evaluate polyCoeff at the known cell positions (via domain FFT) and assert
//    recovered[cellIDs[i]] == cells[i] for all originally supplied i.
// Expected: assertion in step 4 fails for at least one supplied cell, while no
// error was returned by RecoverPolynomialCoefficients, demonstrating the invariant
// break with no equality-preserving guard between the two claimed values.
```

### Citations

**File:** internal/erasure_code/erasure_code.go (L110-148)
```go
func (dr *DataRecovery) RecoverPolynomialCoefficients(data []fr.Element, missingIndices []BlockErasureIndex) ([]fr.Element, error) {
	zX := dr.constructVanishingPolyOnIndices(missingIndices)

	// Compute zX evaluations without mutating zX since we need zX later for a coset FFT
	zXForEval := make([]fr.Element, len(zX))
	copy(zXForEval, zX)
	dr.domainExtended.FftFr(zXForEval)
	zXEval := zXForEval

	if len(zXEval) != len(data) {
		return nil, errors.New("length of data and zXEval should be equal")
	}

	eZEval := make([]fr.Element, len(data))
	for i := 0; i < len(data); i++ {
		eZEval[i].Mul(&data[i], &zXEval[i])
	}

	dr.domainExtended.IfftFr(eZEval)
	dzPoly := eZEval

	dr.domainExtendedCoset.CosetFFtFr(zX)
	cosetZxEval := zX
	dr.domainExtendedCoset.CosetFFtFr(dzPoly)
	cosetDzEVal := dzPoly

	cosetQuotientEval := make([]fr.Element, len(cosetZxEval))
	cosetZxEval = fr.BatchInvert(cosetZxEval)

	for i := 0; i < len(cosetZxEval); i++ {
		cosetQuotientEval[i].Mul(&cosetDzEVal[i], &cosetZxEval[i])
	}

	dr.domainExtendedCoset.CosetIFFtFr(cosetQuotientEval)

	// Truncate the polynomial coefficients to the number of scalars in the data word
	polyCoeff := cosetQuotientEval[:dr.numScalarsInDataWord]
	return polyCoeff, nil
}
```

**File:** api_eip7594.go (L114-126)
```go
	// Check that we have enough cells to perform reconstruction
	if len(cellIDs) < ctx.dataRecovery.NumBlocksNeededToReconstruct() {
		return nil, ErrNotEnoughCellsForReconstruction
	}

	// Find the missing cell IDs and bit reverse them
	// So that they are in normal order
	missingCellIds := make([]uint64, 0, CellsPerExtBlob)
	for cellID := uint64(0); cellID < CellsPerExtBlob; cellID++ {
		if !slices.Contains(cellIDs, cellID) {
			missingCellIds = append(missingCellIds, (domain.BitReverseInt(cellID, CellsPerExtBlob)))
		}
	}
```

**File:** api_eip7594.go (L148-165)
```go
func (ctx *Context) RecoverCellsAndComputeKZGProofs(cellIDs []uint64, cells []*Cell, numGoRoutines int) ([CellsPerExtBlob]*Cell, [CellsPerExtBlob]KZGProof, error) {
	polyCoeff, err := ctx.recoverPolynomialCoeffs(cellIDs, cells)
	if err != nil {
		return [CellsPerExtBlob]*Cell{}, [CellsPerExtBlob]KZGProof{}, err
	}

	recoveredCells, err := ctx.computeCellsFromPolyCoeff(polyCoeff, numGoRoutines)
	if err != nil {
		return [CellsPerExtBlob]*Cell{}, [CellsPerExtBlob]KZGProof{}, err
	}

	proofs, err := ctx.computeKZGProofsFromPolyCoeff(polyCoeff, numGoRoutines)
	if err != nil {
		return [CellsPerExtBlob]*Cell{}, [CellsPerExtBlob]KZGProof{}, err
	}

	return recoveredCells, proofs, nil
}
```
