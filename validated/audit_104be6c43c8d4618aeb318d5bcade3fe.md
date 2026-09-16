[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** api/api_eth.go (L720-720)
```go
	signature := tx.GetTxInternalData().RawSignatureValues()[0]
```

**File:** blockchain/types/tx_signatures.go (L65-74)
```go
func (t TxSignatures) getDefaultSig() (*TxSignature, error) {
	if t.empty() {
		return nil, ErrInvalidSig
	}
	return t[0], nil
}

func (t TxSignatures) empty() bool {
	return len(t) == 0
}
```

**File:** blockchain/types/tx_signatures.go (L91-93)
```go
func (t TxSignatures) ValidateSignature() bool {
	txSig, err := t.getDefaultSig()
	if err != nil {
```
