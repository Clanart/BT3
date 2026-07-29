[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** ante/evm/mono_decorator.go (L117-124)
```go
	if err := txpool.ValidateTransaction(ethTx, &header, decUtils.Signer, &txpool.ValidationOptions{
		Config:  chainConfig,
		Accept:  AcceptedTxType,
		MaxSize: math.MaxUint64, // tx size is checked in cometbft
		MinTip:  new(big.Int),
	}); err != nil {
		return ctx, err
	}
```

**File:** ante/evm/mono_decorator.go (L194-204)
```go
	coreMsg := ethMsg.AsMessage(decUtils.BaseFee)
	if err := CanTransfer(
		ctx,
		md.evmKeeper,
		*coreMsg,
		decUtils.BaseFee,
		decUtils.EvmParams,
		decUtils.Rules.IsLondon,
	); err != nil {
		return ctx, err
	}
```

**File:** utils/utils.go (L200-209)
```go
func Uint256FromBigInt(i *big.Int) (*uint256.Int, error) {
	if i.Sign() < 0 {
		return nil, fmt.Errorf("trying to convert negative *big.Int (%d) to uint256.Int", i)
	}
	result, overflow := uint256.FromBig(i)
	if overflow {
		return nil, fmt.Errorf("overflow trying to convert *big.Int (%d) to uint256.Int (%s)", i, result)
	}
	return result, nil
}
```
