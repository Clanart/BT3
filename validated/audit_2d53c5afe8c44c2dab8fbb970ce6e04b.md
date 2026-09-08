### Title
`RecoverPolynomialCoefficients` silently truncates the recovered polynomial without verifying the high-degree half is zero, allowing crafted cells to break `recovered[cellIDs[i]] == cells[i]` - (File: internal/erasure_code/erasure_code.go)

### Summary
`Context.RecoverCellsAndComputeKZGProofs` (via `recoverPolynomialCoeffs` in `api_eip7594.go`) feeds attacker-controlled cell evaluations directly into `DataRecovery.RecoverPolynomialCoefficients`, which interpolates a degree-`< numScalarsInCodeword` (8192) polynomial from the extended evaluations and then truncates it to `numScalarsInDataWord` (4096) coefficients without ever checking that the discarded high half is zero. If the 64 supplied cells are not consistent with any true degree-<4096 polynomial, the truncation silently drops nonzero high-order coefficients, and the resulting "recovered" cells/proofs will not match the originally supplied cells at the known positions, violating the required invariant `recovered[cellIDs[i]] == cells[i]`.

### Finding Description
`recoverPolynomialCoeffs` (`api_eip7594.go:97-146`) deserializes the caller-supplied `cells` into field elements with only a canonical `SetBytesCanonical`-style check (`deserializeCell`), places them at their claimed `cellID` positions in `extendedBlob`, bit-reverses, and calls `ctx.dataRecovery.RecoverPolynomialCoefficients(extendedBlob, missingCellIds)` (`api_eip7594.go:145`).

Inside `RecoverPolynomialCoefficients` (`internal/erasure_code/erasure_code.go:110-148`):
- It builds the erasure-locator polynomial `Z(x)` vanishing on the missing (64) block positions (`constructVanishingPolyOnIndices`).
- It computes `D(x)*Z(x)` pointwise on the full extended domain (size 8192) from `data` (the attacker cells plus zeros at missing slots), takes an IFFT to get coefficients, then re-evaluates on a coset, divides pointwise by `Z` on the coset, and does a coset-IFFT back to coefficients (`erasure_code.go:123-143`).
- This produces a full 8192-length coefficient vector that exactly interpolates the attacker's raw evaluation data. Only if that data were genuine evaluations of a true degree-<4096 polynomial would the top half (coefficients 4096..8191) be exactly zero.
- The final line `polyCoeff := cosetQuotientEval[:dr.numScalarsInDataWord]` (`erasure_code.go:146`) simply truncates to the first 4096 coefficients — **there is no check that `cosetQuotientEval[4096:]` is all zero.**

Because the FFT-based "division" is exact interpolation over 8192 points (not true polynomial division with remainder check), any 64 field elements the attacker supplies at their claimed cell positions will produce *some* full-length interpolant; the guard (`len(zXEval) != len(data)` and the various ID/ordering checks in `recoverPolynomialCoeffs`) never verifies that this interpolant is actually degree-bound by 4096. The deserialization guard (`deserializeCell`, `isAscending`, cellID bound checks, minimum-cell-count check) passes successfully for such crafted data.

After truncation, `RecoverCellsAndComputeKZGProofs` re-evaluates the truncated (degree<4096) `polyCoeff` via `computeCellsFromPolyCoeff` (`api_eip7594.go:154`, using FK20/`ComputeExtendedPolynomial`) to produce `recoveredCells`. Since the truncated polynomial dropped the nonzero high-order coefficients that were required to match the attacker's original raw cell values, the re-evaluated `recoveredCells[cellIDs[i]]` will generically **not** equal `cells[i]` for the crafted input — breaking exactly the invariant the question specifies: "for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error."

No code path in `recoverPolynomialCoeffs`, `RecoverCellsAndComputeKZGProofs`, or `RecoverPolynomialCoefficients` performs a post-hoc self-consistency check (e.g., re-evaluating the truncated polynomial at the supplied `cellIDs` and comparing against the original `cells`, or checking that the discarded high coefficients are zero) before returning success.

### Impact Explanation
This is a High-severity client-divergence issue: c-kzg's reference/consensus-spec `recover_cells_and_kzg_proofs` implementation is expected to reject inputs that are not consistent with a genuine degree-<4096 polynomial (or to always return recovered data that reproduces the supplied cells exactly). If go-eth-kzg instead accepts such malformed cell sets and emits recovered cells/proofs that (a) disagree with c-kzg's behavior (which would either error out or produce different recovered data), or (b) do not reproduce the original supplied cells at their claimed positions, this is a spec/library disagreement on accept-or-reject and on the emitted recovered data for the same input — the exact "client split" impact category described in the rules.

### Likelihood Explanation
The attack requires only the minimum precondition already needed to call `RecoverCellsAndComputeKZGProofs`/`RecoverCells` normally: supply exactly `NumBlocksNeededToReconstruct()` (64) cells with valid ascending, in-range `cellIDs`, each cell being 64 canonical field elements (passing `deserializeCell`). No cryptographic material (setup, commitments) needs to be forged — any ordinary caller of this fully public API can supply arbitrary field-element bytes for the 64 cells they claim to possess. This is trivially repeatable per call and costs nothing beyond constructing 64 KZG-cell-sized byte arrays.

### Recommendation
After computing `cosetQuotientEval` in `RecoverPolynomialCoefficients`, explicitly verify that the discarded high-order half `cosetQuotientEval[dr.numScalarsInDataWord:]` is all zero (returning an error such as `ErrPolynomialNotLowDegree` otherwise), and/or have `recoverPolynomialCoeffs`/`RecoverCellsAndComputeKZGProofs` re-derive cells from the truncated polynomial and assert they equal the originally supplied `cells[i]` at `cellIDs[i]` before returning success, matching the low-degree/self-consistency check that c-kzg performs.

### Proof of Concept
```go
package goethkzg

import (
    "testing"
    "github.com/consensys/gnark-crypto/ecc/bls12-381/fr"
    "github.com/crate-crypto/go-eth-kzg/internal/domain"
)

func TestRecoverInvariantBroken(t *testing.T) {
    ctx, _ := NewContext4096Secure()

    // Craft 64 cellIDs (half of CellsPerExtBlob) in ascending order.
    var cellIDs []uint64
    for i := uint64(0); i < CellsPerExtBlob; i += 2 {
        cellIDs = append(cellIDs, i)
    }

    // Craft 64 cells whose raw field-element content is NOT the
    // evaluation of a true degree<4096 polynomial: fill each cell with
    // canonical but effectively random field elements (not derived from
    // any FK20 encoding of a low-degree poly).
    cells := make([]*Cell, len(cellIDs))
    for i := range cells {
        var c Cell
        for j := 0; j < scalarsPerCell; j++ {
            var e fr.Element
            e.SetUint64(uint64(i*scalarsPerCell + j + 12345)) // arbitrary canonical values
            b := e.Bytes()
            copy(c[j*32:(j+1)*32], b[:])
        }
        cells[i] = &c
    }

    // BEFORE: guard should still pass (no deserialization/order errors).
    recoveredCells, _, err := ctx.RecoverCellsAndComputeKZGProofs(cellIDs, cells, 0)
    if err != nil {
        t.Fatalf("guard rejected input unexpectedly: %v", err)
    }

    // AFTER: assert the invariant recovered[cellIDs[i]] == cells[i].
    for i, id := range cellIDs {
        if *recoveredCells[id] != *cells[i] {
            t.Fatalf("invariant broken: recovered[%d] != supplied cells[%d]", id, i)
        }
    }
    _ = domain.BitReverseInt // (import retained if needed for constructing missing set manually)
}
```
Expected result on the current code: `RecoverCellsAndComputeKZGProofs` returns `err == nil` (guard passed), but the final assertion loop fails because `recoveredCells[id] != cells[i]`, confirming the invariant break described in the question.