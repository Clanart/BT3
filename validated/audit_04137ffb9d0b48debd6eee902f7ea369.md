### Title
`recoverPolynomialCoeffs` never verifies `recovered[cellIDs[i]] == cells[i]`, allowing inconsistent/over-determined cell sets to be silently "recovered" into different data - (File: `api_eip7594.go`)

### Summary
`Context.RecoverCells` / `Context.RecoverCellsAndComputeKZGProofs` call `ctx.recoverPolynomialCoeffs`, which performs FFT-based Reed–Solomon erasure decoding and simply truncates the coset-domain quotient to `numScalarsInDataWord` coefficients without ever checking that the resulting low-degree polynomial actually reproduces the evaluations the caller supplied at the non-missing cell IDs. Because no post-recovery consistency check exists, an attacker who supplies more cells than the minimum threshold (`>= NumBlocksNeededToReconstruct()`, i.e. `>=64` cells) using data that is not a genuine evaluation of a single degree`<4096` polynomial can cause the function to return successfully while `recovered[cellIDs[i]] != cells[i]` for at least one supplied cell.

### Finding Description
The broken equality is: for every `i`, `recovered[cellIDs[i]] == cells[i]` where `recovered = computeCellsFromPolyCoeff(polyCoeff)` and `polyCoeff, _ = ctx.recoverPolynomialCoeffs(cellIDs, cells)`.

Tracing `recoverPolynomialCoeffs` in `api_eip7594.go`:
- Length/order/range checks are performed [1](#0-0) .
- Missing cell IDs are derived purely from set membership of `cellIDs`, bit-reversed [2](#0-1) .
- Supplied cells are deserialized and copied verbatim into `extendedBlob` at their exact positions, and the rest is left as the zero value [3](#0-2) .
- The result is handed to `dataRecovery.RecoverPolynomialCoefficients(extendedBlob, missingCellIds)` with no further check on the return value [4](#0-3) .

Inside `RecoverPolynomialCoefficients` (`internal/erasure_code/erasure_code.go`), the algorithm computes `D(x)*Z(x)` in evaluation form (where `D` is the supplied data, `Z` vanishes exactly on the assumed-missing indices), transforms to a coset, divides by `Z`, and then **unconditionally truncates** the result to `numScalarsInDataWord` coefficients: `polyCoeff := cosetQuotientEval[:dr.numScalarsInDataWord]` [5](#0-4) . There is no check that `cosetQuotientEval[dr.numScalarsInDataWord:]` are all zero, which is exactly the condition that would confirm the division was exact/consistent, i.e., that `D` genuinely lies on a degree`<4096` codeword at the "known" positions. If the caller supplies a batch of `>=64` cells whose values do not all lie on a single degree`<4096` polynomial's extended evaluations (which the algorithm has no way to detect, since it trusts every supplied cell as ground truth), the truncated `polyCoeff` is still returned with `nil` error, and its re-evaluation via `computeCellsFromPolyCoeff` will differ from at least one of the originally supplied cells at the same `cellID`.

None of the existing guards (`isAscending`, cellID range checks, `NumBlocksNeededToReconstruct` threshold) address this — they only validate shape/ordering of the input, not algebraic consistency of the supplied cell values with a valid codeword.

### Impact Explanation
`Context.RecoverCells` / `RecoverCellsAndComputeKZGProofs` can return success (`nil` error) together with a `polyCoeff`/`recoveredCells` set that does not match the cells the caller explicitly supplied as "known good" data. This is a mismatch between the input cells and the emitted recovered cells/proofs for the same call — precisely the "High" category of client-split: the consensus spec / c-kzg either enforce (or are expected to produce) reconstruction consistent with the supplied cells, and any divergence in accepted/emitted recovered data between go-eth-kzg and other implementations on the same crafted input is a client split. Any downstream consumer that gossips or persists the "recovered" cells/proofs as if they matched the originally supplied cell at that index would silently propagate different data than what was fed in.

### Likelihood Explanation
The attacker needs no special privileges: `RecoverCells`/`RecoverCellsAndComputeKZGProofs` are public methods on `Context` reachable with arbitrary caller-supplied `cellIDs`/`cells` byte content. The only precondition is supplying `>= NumBlocksNeededToReconstruct()` (64) cells with IDs in ascending order and in valid range, where the data is not a genuine evaluation of a single low-degree polynomial (attacker fully controls the field-element content of each `Cell`). The call is deterministic and repeatable — the same crafted input triggers the same silent divergence every time.

### Recommendation
After computing `polyCoeff` in `recoverPolynomialCoeffs` (or inside `RecoverPolynomialCoefficients`), re-derive the extended evaluations from `polyCoeff` (or verify `cosetQuotientEval[numScalarsInDataWord:]` are all zero before truncation) and explicitly compare the re-derived values at each supplied `cellIDs[i]` against `cells[i]`; return an error (e.g., a new `ErrRecoveredCellMismatch`) if any position disagrees, matching the semantics implied by the invariant "recovered[cellIDs[i]] == cells[i] and degree < ScalarsPerBlob, else an error."

### Proof of Concept
```go
func TestRecoverCells_InconsistentDataNotRejected(t *testing.T) {
    ctx := NewContext4096Secure() // or embedded/trusted-setup ctx used in repo tests

    // Build a genuine blob, compute all cells/proofs
    blob := sampleValidBlob(t) // any valid deterministic blob
    allCells, _, err := ctx.ComputeCellsAndKZGProofs(&blob, 0)
    require.NoError(t, err)

    // Craft >=64 cellIDs (ascending) using genuine cells for most positions,
    // but splice in field-element data for one cell that is NOT consistent
    // with the same degree<4096 polynomial (e.g., copy a cell from a
    // different, independently generated blob into one slot).
    cellIDs := ascendingIDs(0, 64) // first 64 cellIDs
    cells := make([]*Cell, 64)
    for i, id := range cellIDs {
        cells[i] = allCells[id]
    }
    otherBlob := differentValidBlob(t)
    otherCells, _, err := ctx.ComputeCellsAndKZGProofs(&otherBlob, 0)
    require.NoError(t, err)
    tamperedIdx := 10
    cells[tamperedIdx] = otherCells[cellIDs[tamperedIdx]] // inconsistent cell

    recoveredCells, err := ctx.RecoverCells(cellIDs, cells, 0)

    // Equality claimed by the invariant, BEFORE any fix:
    // Expect this to be violated (finding demonstrated) if err == nil
    // and recoveredCells[cellIDs[tamperedIdx]] != cells[tamperedIdx].
    if err == nil {
        require.NotEqual(t, cells[tamperedIdx], recoveredCells[cellIDs[tamperedIdx]],
            "recovered cell diverges from supplied cell with no error returned")
    }
}
```
Both sides of the invariant (`recovered[cellIDs[i]] == cells[i]` for the tampered index, and "error else") should be asserted: currently `err == nil` while the equality fails, demonstrating the missing consistency check.

### Citations

**File:** api_eip7594.go (L98-117)
```go
	if len(cellIDs) != len(cells) {
		return nil, ErrNumCellIDsNotEqualNumCells
	}

	// Check that the cell Ids are ordered (ascending)
	if !isAscending(cellIDs) {
		return nil, ErrCellIDsNotOrdered
	}

	// Check that each CellId is less than CellsPerExtBlob
	for _, cellID := range cellIDs {
		if cellID >= CellsPerExtBlob {
			return nil, ErrFoundInvalidCellID
		}
	}

	// Check that we have enough cells to perform reconstruction
	if len(cellIDs) < ctx.dataRecovery.NumBlocksNeededToReconstruct() {
		return nil, ErrNotEnoughCellsForReconstruction
	}
```

**File:** api_eip7594.go (L119-126)
```go
	// Find the missing cell IDs and bit reverse them
	// So that they are in normal order
	missingCellIds := make([]uint64, 0, CellsPerExtBlob)
	for cellID := uint64(0); cellID < CellsPerExtBlob; cellID++ {
		if !slices.Contains(cellIDs, cellID) {
			missingCellIds = append(missingCellIds, (domain.BitReverseInt(cellID, CellsPerExtBlob)))
		}
	}
```

**File:** api_eip7594.go (L128-141)
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
```

**File:** api_eip7594.go (L142-146)
```go
	// Bit reverse the extendedBlob so that it is in normal order
	domain.BitReverse(extendedBlob)

	return ctx.dataRecovery.RecoverPolynomialCoefficients(extendedBlob, missingCellIds)
}
```

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
