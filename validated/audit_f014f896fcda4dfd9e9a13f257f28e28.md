[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** internal/kzg_multi/fk20/toeplitz.go (L11-29)
```go
type toeplitzMatrix struct {
	col []fr.Element
	row []fr.Element
}

// Embed toeplitz matrix within a circulant matrix
func (tm *toeplitzMatrix) embedCirculant() circulantMatrix {
	n := len(tm.row)
	row := make([]fr.Element, len(tm.col)+n)

	// Copy tm.Col
	copy(row, tm.col)

	// Append rotated and reversed tm.Row
	for i := 1; i < n; i++ {
		row[len(tm.col)+i] = tm.row[(n-i)%n]
	}
	return circulantMatrix{row: row}
}
```

**File:** internal/kzg_multi/fk20/toeplitz.go (L42-93)
```go
type BatchToeplitzMatrixVecMul struct {
	transposedFFTFixedVectors [][]bls12381.G1Affine
	circulantDomain           domain.Domain
}

// newBatchToeplitzMatrixVecMul creates a new Instance of `BatchToeplitzMatrixVecMul`
//
// Note: `fixedVectors` is mutated in place, ie it is treated as mutable reference to a pointer.
func newBatchToeplitzMatrixVecMul(fixedVectors [][]bls12381.G1Affine) BatchToeplitzMatrixVecMul {
	// We assume that the length of the vector is at least one.
	// If this is not true, then we panic on startup.
	//
	// Check that these vectors have a power of two size
	size := len(fixedVectors[0])
	for i := 1; i < len(fixedVectors); i++ {
		if size != len(fixedVectors[i]) {
			panic("all vectors must be the same size")
		}
	}

	// Check that the size is a power of two
	// This just makes padding to the next power of two
	// simple.
	if !utils.IsPowerOfTwo(uint64(size)) {
		panic("fixedVectors do not have a power of two size")
	}

	// We assume that all vectors have the same size
	vecLen := size

	// Given we force the toeplitz matrix to be a power of two.
	// Embedding the toeplitz matrix into a circulant matrix
	// will produce a circulant matrix whose row is twice the size
	// of the toeplitz matrix.
	circulantPaddedVecSize := vecLen * 2

	circulantDomain := domain.NewDomain(uint64(circulantPaddedVecSize))

	fftFixedVectors := fixedVectors
	// Before performing the fft, pad the vector so that it is the correct size.
	padToPowerOfTwo(fftFixedVectors)

	for i := 0; i < len(fftFixedVectors); i++ {
		circulantDomain.FftG1(fftFixedVectors[i])
	}
	transposedFFTFixedVectors := transposeVectors(fftFixedVectors)

	return BatchToeplitzMatrixVecMul{
		transposedFFTFixedVectors: transposedFFTFixedVectors,
		circulantDomain:           *circulantDomain,
	}
}
```

**File:** internal/erasure_code/erasure_code.go (L75-90)
```go
func (dr *DataRecovery) constructVanishingPolyOnIndices(missingBlockErasureIndices []BlockErasureIndex) []fr.Element {
	// Collect all of the roots that are associated with the missing block erasure indices
	missingBlockErasureIndexRoots := make([]fr.Element, len(missingBlockErasureIndices))
	for i, index := range missingBlockErasureIndices {
		missingBlockErasureIndexRoots[i] = dr.rootsOfUnityBlockErasureIndex.Roots[index]
	}

	shortZeroPoly := vanishingPolyCoeff(missingBlockErasureIndexRoots)

	zeroPolyCoeff := make([]fr.Element, dr.numScalarsInCodeword)
	for i, coeff := range shortZeroPoly {
		zeroPolyCoeff[i*dr.blockErasureSize] = coeff
	}

	return zeroPolyCoeff
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

**File:** internal/erasure_code/erasure_code.go (L151-164)
```go
func vanishingPolyCoeff(xs []fr.Element) poly.PolynomialCoeff {
	result := []fr.Element{fr.One()}

	for _, x := range xs {
		// This is to silence: G601: Implicit memory aliasing in for loop.
		x := x

		negX := fr.Element{}
		negX.Neg(&x)
		result = poly.PolyMul(result, []fr.Element{negX, fr.One()})
	}

	return result
}
```
