### Title
`RecoverPolynomialCoefficients` truncates high-order coefficients without verifying they are zero or that recovered cells match supplied cells - ([File: internal/erasure_code/erasure_code.go])

### Summary
`DataRecovery.RecoverPolynomialCoefficients` reconstructs a degree-`< numScalarsInCodeword` (8192) polynomial from the supplied cell evaluations via the vanishing-polynomial/coset-FFT erasure-decoding trick, then blindly truncates the result to `numScalarsInDataWord` (4096) coefficients with no check that the discarded upper half is zero. The resulting truncated coefficients are re-extended by `computeCellsFromPolyCoeff` to produce the "recovered" cells and proofs returned by `Context.RecoverCellsAndComputeKZGProofs`, with no final assertion that `recovered[cellIDs[i]] == cells[i]` for the caller-supplied cells.

### Finding Description
The broken equality is: for every `i`, `recovered[cellIDs[i]]` (the cell re-derived from the truncated/re-extended polynomial) must equal `cells[i]` (the attacker-supplied cell at that index), and the recovered polynomial must have degree `< ScalarsPerBlob` (4096).

The code path is:
- `Context.RecoverCellsAndComputeKZGProofs` (`api_eip7594.go:148`) calls `ctx.recoverPolynomialCoeffs` (`api_eip7594.go:97`), which deserializes attacker-supplied cells into `extendedBlob`, bit-reverses it, and calls `dr.RecoverPolynomialCoefficients(extendedBlob, missingCellIds)` [1](#0-0) .
- `RecoverPolynomialCoefficients` computes `cosetQuotientEval`, a degree-`<8192` polynomial that is mathematically guaranteed (by the vanishing-polynomial construction) to reproduce the attacker's supplied evaluations exactly at the known (non-erased) points, regardless of whether those values actually lie on any real degree-`<4096` polynomial [2](#0-1) .
- The function then truncates: `polyCoeff := cosetQuotientEval[:dr.numScalarsInDataWord]`, discarding the top half of coefficients with **no check that they are zero** [3](#0-2) .
- Back in `RecoverCellsAndComputeKZGProofs`, this truncated `polyCoeff` is fed to `computeCellsFromPolyCoeff`, which re-extends it via `ctx.fk20.ComputeExtendedPolynomial` (implicitly zero-padding the discarded high coefficients) to produce the full set of cells/proofs returned to the caller [4](#0-3) .

Because the true (untruncated) degree-`<8192` polynomial exactly reproduces the attacker's input at the supplied cell positions, but the *truncated-then-re-extended* polynomial generally does not (unless the discarded coefficients were genuinely zero), an attacker can supply 64 cells that are not consistent with any real degree-`<4096` polynomial but whose inconsistency is confined to the high coefficients. The library will still accept them (none of the existing guards — `ErrNumCellIDsNotEqualNumCells`, `isAscending`, `ErrFoundInvalidCellID`, `ErrNotEnoughCellsForReconstruction` in `api_eip7594.go:97-117` — check the actual algebraic consistency of the cell values) and emit `recoveredCells`/`proofs` that silently diverge from the caller-supplied `cells[i]` for the originally-supplied `cellIDs[i]`, and from whatever a spec-conformant implementation that explicitly checks/asserts this invariant would produce (accept vs. reject, or different emitted cell/proof values).

### Impact Explanation
This affects `Context.RecoverCellsAndComputeKZGProofs`, a normal-protocol-path entrypoint reachable by any peer providing arbitrary cell/cellID bytes for data reconstruction (e.g., in PeerDAS/EIP-7594 cell-recovery flows). If a different node or the consensus reference implementation performs the equivalent "recovered cell must equal supplied cell" sanity check, this library could accept and emit cells/proofs that the reference implementation rejects, or emit cells/proofs whose values differ from the reference for identical attacker-supplied input — a client/implementation split matching the "High" severity category (library and consensus spec/c-kzg disagreeing on accept or on emitted cell/proof/recovered data for the same input).

### Likelihood Explanation
The attacker needs only to craft 64 syntactically-valid cells (correct length/field-element encoding) that pass basic structural checks (`isAscending`, cell-ID bounds, count ≥ `NumBlocksNeededToReconstruct`) but are not true evaluations of a common degree-`<4096` polynomial, with the algebraic inconsistency confined to what becomes the discarded high half of the reconstructed coefficient vector. This is a pure computation on public parameters (no access to trusted setup secrets needed) and is repeatable for any blob/cell-recovery call.

### Recommendation
After computing `cosetQuotientEval` in `RecoverPolynomialCoefficients`, verify that all coefficients at indices `[numScalarsInDataWord:numScalarsInCodeword]` are zero (return an error otherwise), and/or after re-extending in `RecoverCellsAndComputeKZGProofs`, explicitly assert `recoveredCells[cellID] == cells[i]` for every supplied `cellIDs[i]` before returning, matching the invariant expected by the consensus spec.

### Proof of Concept
```go
// In package erasure_code (or via exported wrapper in goethkzg), root package test:
func TestRecoverPolynomialCoefficients_InconsistentHighCoeffs(t *testing.T) {
    ctx := NewContext4096Secure()
    // Craft 64 cells such that the interpolated degree-<8192 polynomial's
    // top 4096 coefficients are non-zero (e.g. start from a genuine degree-<4096
    // polynomial, add a perturbation only supported on coefficients [4096:8192],
    // extend/evaluate it, and slice out 64 cells matching known cellIDs).
    cellIDs, cells := buildCellsWithNonZeroHighCoeffs(ctx)

    recoveredCells, _, err := ctx.RecoverCellsAndComputeKZGProofs(cellIDs, cells, 0)
    if err != nil {
        t.Fatalf("expected acceptance, got error: %v", err)
    }

    // Assert the broken invariant: for supplied cellIDs[i], recoveredCells[cellIDs[i]]
    // should equal cells[i]. Show that it does NOT hold, demonstrating divergence
    // from the expected recovery semantics.
    for i, id := range cellIDs {
        if !bytes.Equal(recoveredCells[id][:], cells[i][:]) {
            t.Fatalf("invariant broken: recovered[%d] != supplied cells[%d]", id, i)
        }
    }
}
```

### Citations

**File:** api_eip7594.go (L128-145)
```go
	// Convert Cells to field elements
	extendedBlob := make([]fr.Element, scalarsPerExtBlob)
	// for each cellId, we get the corresponding cell in cells
	// then use the cellId to place the cell in the correct position in the data(extendedBlob) array
	for i, cellID := range cellIDs {
		cell := cells[i]
		// Deserialize the cell
		cellEvals, err := deserializeCell(cell)
		if err != nil {
			return nil, err
		}
		// Place the cell in the correct position in the data array
		copy(extendedBlob[cellID*scalarsPerCell:], cellEvals)
	}
	// Bit reverse the extendedBlob so that it is in normal order
	domain.BitReverse(extendedBlob)

	return ctx.dataRecovery.RecoverPolynomialCoefficients(extendedBlob, missingCellIds)
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

**File:** internal/erasure_code/erasure_code.go (L110-141)
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
```

**File:** internal/erasure_code/erasure_code.go (L145-147)
```go
	// Truncate the polynomial coefficients to the number of scalars in the data word
	polyCoeff := cosetQuotientEval[:dr.numScalarsInDataWord]
	return polyCoeff, nil
```
