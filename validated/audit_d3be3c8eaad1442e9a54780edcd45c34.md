[1](#0-0) [2](#0-1)

### Citations

**File:** x/erc20/genesis.go (L33-38)
```go
	for _, pair := range data.TokenPairs {
		err := k.SetToken(ctx, pair)
		if err != nil {
			return
		}
	}
```

**File:** x/erc20/keeper/token_pairs.go (L34-45)
```go
func (k *Keeper) SetToken(ctx sdk.Context, pair types.TokenPair) error {
	if k.IsDenomRegistered(ctx, pair.Denom) {
		return errorsmod.Wrapf(types.ErrTokenPairAlreadyExists, "token already exists for denom %s", pair.Denom)
	}
	if k.IsERC20Registered(ctx, pair.GetERC20Contract()) {
		return errorsmod.Wrapf(types.ErrTokenPairAlreadyExists, "token already exists for token %s", pair.Erc20Address)
	}
	k.SetTokenPair(ctx, pair)
	k.SetDenomMap(ctx, pair.Denom, pair.GetID())
	k.SetERC20Map(ctx, pair.GetERC20Contract(), pair.GetID())
	return nil
}
```
