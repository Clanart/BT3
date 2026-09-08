Based on my analysis, this claim does not hold up as an in-scope vulnerability.

The recovery algorithm in `RecoverPolynomialCoefficients` [1](#0-0)  implements the standard FFT-based Reed-Solomon erasure-decoding trick: it builds the vanishing polynomial `zX` over the *missing* indices, multiplies pointwise by the (zero-padded) received data, IFFTs, divides by `zX` on a coset, and truncates to the first `numScalarsInDataWord` coefficients. This construction is only guaranteed to be an exact interpolant through the *provided* points when those points are all mutually consistent with a single polynomial of degree `< numScalarsInDataWord`. When exactly half the cells are provided (the minimum threshold, `NumBlocksNeededToReconstruct()`), the decoder is doing exact-fit erasure recovery, not error-correction — it has no redundancy to detect or reject an inconsistent point.

If one of the 64 supplied "genuine" positions actually deviates from the shared low-degree polynomial implied by the other 63 (i.e., the cell bytes are canonical field elements per `deserializeCell` [2](#0-1)  but are not a true evaluation on that coset), then `D(X)` — the exact interpolant of the zero-padded/received data — is **not** divisible by `zX(X)`, so the pointwise coset "quotient" and truncation step will generally produce a polynomial that does **not** reproduce the crafted cell's value at that coset (and can also perturb other reconstructed cells due to aliasing in the truncation). So the technical premise that `recovered[cellIDs[i]] == cells[i]` can fail for the crafted index is plausible.

However, this is not a bug particular to this repo, nor a divergence from the consensus spec / c-kzg. Per the EIP-7594 design (mirrored in the consensus-specs reference implementation), `recover_cells_and_kzg_proofs` / `RecoverCellsAndComputeKZGProofs` has an explicit precondition: **the caller must have already verified each supplied cell against its commitment (via `verify_cell_kzg_proof_batch` / `VerifyCellKZGProofBatch`) before calling recovery** [3](#0-2) . The recovery function itself performs no such verification — matching the reference Python spec and c-kzg-4844 exactly, both of which use the identical FFT-based erasure-decoding algorithm with the identical "garbage in, garbage out" behavior for inconsistent inputs. Since this library's behavior for a given (possibly-inconsistent) input matches the spec/c-kzg's behavior for the same input, there is no cross-implementation disagreement, and the finding falls into the explicitly out-of-scope categories: "publicly known issues" / "theoretical findings" about a documented library precondition rather than an implementation defect. `recoverPolynomialCoeffs` [4](#0-3)  only performs bookkeeping checks (`isAscending`, cell-ID bounds, count vs. `NumBlocksNeededToReconstruct`), by design, not cross-cell consistency checks — this is intentional and spec-conformant, not a divergence.

#No vulnerability found for this question.

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

**File:** serialization.go (L227-247)
```go
func deserializeCell(cell *Cell) ([]fr.Element, error) {
	if cell == nil {
		return nil, ErrDeserializeNilInput
	}
	evals := make([]fr.Element, scalarsPerCell)

	for i := 0; i < scalarsPerCell; i++ {
		chunk := cell[i*SerializedScalarSize : (i+1)*SerializedScalarSize]

		chunk_arr := [SerializedScalarSize]byte{}
		copy(chunk_arr[:], chunk)

		eval, err := DeserializeScalar(chunk_arr)
		if err != nil {
			return nil, err
		}
		evals[i] = eval
	}

	return evals, nil
}
```

**File:** api_eip7594.go (L97-146)
```go
func (ctx *Context) recoverPolynomialCoeffs(cellIDs []uint64, cells []*Cell) ([]fr.Element, error) {
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

	// Find the missing cell IDs and bit reverse them
	// So that they are in normal order
	missingCellIds := make([]uint64, 0, CellsPerExtBlob)
	for cellID := uint64(0); cellID < CellsPerExtBlob; cellID++ {
		if !slices.Contains(cellIDs, cellID) {
			missingCellIds = append(missingCellIds, (domain.BitReverseInt(cellID, CellsPerExtBlob)))
		}
	}

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
}
```

**File:** api_eip7594.go (L167-219)
```go
func (ctx *Context) VerifyCellKZGProofBatch(commitments []KZGCommitment, cellIndices []uint64, cells []*Cell, proofs []KZGProof) error {
	rowCommitments, rowIndices := deduplicateKZGCommitments(commitments)

	// Check that all components in the batch have the same size, expect the rowCommitments
	batchSize := len(rowIndices)
	lengthsAreEqual := batchSize == len(cellIndices) && batchSize == len(cells) && batchSize == len(proofs)
	if !lengthsAreEqual {
		return ErrBatchLengthCheck
	}

	if batchSize == 0 {
		return nil
	}

	// Check that the row indices do not exceed len(rowCommitments)
	for _, rowIndex := range rowIndices {
		if rowIndex >= uint64(len(rowCommitments)) {
			return ErrInvalidRowIndex
		}
	}

	for _, cellIndex := range cellIndices {
		if cellIndex >= CellsPerExtBlob {
			return ErrInvalidCellID
		}
	}

	commitmentsG1 := make([]bls12381.G1Affine, len(rowCommitments))
	for i := 0; i < len(rowCommitments); i++ {
		comm, err := DeserializeKZGCommitment(rowCommitments[i])
		if err != nil {
			return err
		}
		commitmentsG1[i] = comm
	}
	proofsG1 := make([]bls12381.G1Affine, len(proofs))
	for i := 0; i < len(proofs); i++ {
		proof, err := DeserializeKZGProof(proofs[i])
		if err != nil {
			return err
		}
		proofsG1[i] = proof
	}
	cosetsEvals := make([][]fr.Element, len(cells))
	for i := 0; i < len(cells); i++ {
		cosetEvals, err := deserializeCell(cells[i])
		if err != nil {
			return err
		}
		cosetsEvals[i] = cosetEvals
	}
	return kzgmulti.VerifyMultiPointKZGProofBatch(commitmentsG1, rowIndices, cellIndices, proofsG1, cosetsEvals, ctx.openKey7594)
}
```
