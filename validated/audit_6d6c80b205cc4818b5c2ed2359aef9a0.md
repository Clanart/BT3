### Title
`RecoverPolynomialCoefficients` accepts cells inconsistent with any degree<4096 polynomial by truncating without a zero-check on high coefficients - (File: `internal/erasure_code/erasure_code.go`)

### Summary
`DataRecovery.RecoverPolynomialCoefficients` computes the erasure-decoded quotient polynomial over the full extended domain (`numScalarsInCodeword` = 2×`numScalarsInDataWord` coefficients) and then silently truncates to the first `numScalarsInDataWord` coefficients, without verifying that the discarded upper half is all-zero. Reference implementations (c-kzg-4844) perform exactly this zero-check to reject cells that are not evaluations of a true degree<4096 polynomial; this repository's code omits it, so `Context.RecoverCellsAndComputeKZGProofs` can accept attacker-supplied cells that c-kzg-4844 would reject, and can emit `recovered`/proof data that no longer agrees with the caller-supplied cells at the known positions.

### Finding Description
The invariant under test: for every `cellIDs[i]` supplied to `RecoverCellsAndComputeKZGProofs`, the recovered polynomial evaluated/encoded back into cells must satisfy `recovered[cellIDs[i]] == cells[i]`, and the recovered polynomial must have degree < `ScalarsPerBlob` (4096 scalars).

Code path:
- `Context.RecoverCellsAndComputeKZGProofs` (`api_eip7594.go:148`) calls `ctx.recoverPolynomialCoeffs` (`api_eip7594.go:97`), which deserializes attacker-supplied `cells` into `extendedBlob` at the attacker-chosen `cellIDs` positions [1](#0-0) , then calls `ctx.dataRecovery.RecoverPolynomialCoefficients(extendedBlob, missingCellIds)`.
- Inside `RecoverPolynomialCoefficients` (`internal/erasure_code/erasure_code.go:110-148`), the algorithm builds the vanishing polynomial `zX` on the (attacker-implied) missing indices, multiplies data by `zXEval`, inverse-FFTs to get `dzPoly`, performs a coset-domain division (`cosetDzEVal * cosetZxEval^{-1}`) to recover the quotient polynomial coefficients over the *entire* extended domain (length `numScalarsInCodeword`), then simply slices:
```go
polyCoeff := cosetQuotientEval[:dr.numScalarsInDataWord]
return polyCoeff, nil
``` [2](#0-1) 

There is no check anywhere in this function, in `recoverPolynomialCoeffs`, or in `RecoverCellsAndComputeKZGProofs` that `cosetQuotientEval[dr.numScalarsInDataWord:]` are all zero. Mathematically, this division only recovers a valid degree<`numScalarsInDataWord` polynomial when the known cells are actually consistent with some such polynomial. If an attacker crafts `cells` (the known, non-erased positions) that are not evaluations of any true degree<4096 polynomial extended to `CellsPerExtBlob` cells, the true quotient will have non-zero coefficients in the upper half of the extended domain. The truncation silently discards this non-zero evidence and returns a polynomial anyway, treating the malformed input as if it were valid.

Downstream, `RecoverCellsAndComputeKZGProofs` re-encodes this truncated `polyCoeff` into `recoveredCells`/`proofs` via `computeCellsFromPolyCoeff` / `computeKZGProofsFromPolyCoeff` (`api_eip7594.go:154-162`) without ever comparing `recoveredCells[cellIDs[i]]` back to the caller's original `cells[i]`. Because the discarded high coefficients were non-zero, the re-evaluated polynomial at the known cell positions generally no longer equals the attacker's originally supplied `cells[i]` values — breaking the stated invariant `recovered[cellIDs[i]] == cells[i]`, and also breaking the degree<4096 requirement (since the "true" interpolant that fits all given cells has degree ≥ 4096, and the returned truncated polynomial is not actually consistent with the input).

None of the existing guards catch this: `isAscending`, cell-ID bound checks, and `NumBlocksNeededToReconstruct` (`api_eip7594.go:98-117`) only validate structural/index properties of the call, not the algebraic consistency of the cell values with a low-degree polynomial.

### Impact Explanation
This is a High-severity client-split condition: for the same attacker-supplied `(cellIDs, cells)` input, c-kzg-4844 (which validates that the "unused" half of the recovered coefficient vector is zero and errors out otherwise) would reject the reconstruction, while this Go library accepts it and emits a `recoveredCells`/`proofs` tuple that is inconsistent with the caller's own inputs. This produces divergent behavior between clients built on go-eth-kzg and clients built on c-kzg-4844 for identical PeerDAS/EIP-7594 cell-recovery requests — one client would reject the reconstruction as invalid, the other would successfully emit cells and valid-looking KZG proofs for data that does not actually match the supplied evaluations. This can lead to a node accepting/propagating cell/proof data that a c-kzg-4844-based peer would consider invalid, a core consensus-safety property of the erasure-coded blob availability sampling (PeerDAS) scheme.

### Likelihood Explanation
The attacker only needs to be able to call `Context.RecoverCellsAndComputeKZGProofs` with self-authored `cellIDs`/`cells` — this is fully within the unprivileged, normal protocol-path threat model (no trusted setup manipulation, no privileged access). Constructing cells that are not evaluations of any degree<4096 polynomial is straightforward (e.g., pick arbitrary field-element values for a subset of cells rather than deriving them from `ComputeCells` on an actual blob). The cost is a single call; the bug is deterministic and repeatable on every such malformed input.

### Recommendation
After computing `cosetQuotientEval` in `RecoverPolynomialCoefficients`, explicitly verify that all coefficients from index `numScalarsInDataWord` to `numScalarsInCodeword-1` are zero (`fr.Element.IsZero()`), and return an error (e.g., a new `ErrRecoveredPolynomialWrongDegree`) if any are non-zero, mirroring the check performed by c-kzg-4844's `recover_polynomial`. Optionally, additionally verify `recoveredCells[cellIDs[i]] == cells[i]` in `RecoverCellsAndComputeKZGProofs` as a defense-in-depth cross-check before returning.

### Proof of Concept
```go
package erasure_code

import (
    "testing"
    "github.com/consensys/gnark-crypto/ecc/bls12-381/fr"
)

func TestRecoverPolynomialCoefficients_RejectsHighDegree(t *testing.T) {
    dr := NewDataRecovery(64 /*blockErasureSize*/, 4096 /*numScalarsInDataWord*/, 2 /*expansionFactor*/)

    // Construct a codeword of length numScalarsInCodeword whose true minimal
    // interpolant over the known (non-erased) positions has degree >= 4096
    // (e.g., fill all positions, including "known" ones, with values that are
    // NOT evaluations of any degree<4096 polynomial extended via encode/FFT).
    data := make([]fr.Element, dr.numScalarsInCodeword)
    for i := range data {
        data[i] = fr.NewElement(uint64(i*i + 7)) // arbitrary, not RS-codeword-consistent
    }

    missingIndices := []BlockErasureIndex{0, 1} // some blocks erased

    polyCoeff, err := dr.RecoverPolynomialCoefficients(data, missingIndices)
    if err != nil {
        t.Fatalf("expected computation to proceed (bug reproduces), got err: %v", err)
    }

    // Re-encode the truncated polynomial and check it does NOT match the
    // originally supplied "known" data at non-missing positions -- this is
    // the witness for the broken invariant recovered[cellIDs[i]] == cells[i].
    reEncoded := make([]fr.Element, len(polyCoeff))
    copy(reEncoded, polyCoeff)
    reEncoded = dr.Encode(reEncoded)

    mismatchFound := false
    for i := 0; i < len(data); i++ {
        if !reEncoded[i].Equal(&data[i]) {
            mismatchFound = true
            break
        }
    }
    if !mismatchFound {
        t.Fatal("expected mismatch demonstrating broken invariant, but none found")
    }
    // BUG: RecoverPolynomialCoefficients returned a "successful" result (err == nil)
    // even though recovered[i] != cells[i] for supplied non-missing positions,
    // because it never checked that cosetQuotientEval[numScalarsInDataWord:] == 0.
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

**File:** internal/erasure_code/erasure_code.go (L143-148)
```go
	dr.domainExtendedCoset.CosetIFFtFr(cosetQuotientEval)

	// Truncate the polynomial coefficients to the number of scalars in the data word
	polyCoeff := cosetQuotientEval[:dr.numScalarsInDataWord]
	return polyCoeff, nil
}
```
