[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** x/vm/types/denom_config.go (L98-122)
```go
// setEVMCoinInfo allows to define denom and decimals of the coin used in the EVM.
func setEVMCoinInfo(eci EvmCoinInfo) error {
	if evmCoinInfo != nil {
		return errors.New("EVM coin info already set")
	}

	if Decimals(eci.Decimals) == EighteenDecimals {
		if eci.Denom != eci.ExtendedDenom {
			return errors.New("EVM coin denom and extended denom must be the same for 18 decimals")
		}
	}

	evmCoinInfo = new(EvmCoinInfo)

	if err := setEVMCoinDenom(eci.Denom); err != nil {
		return err
	}
	if err := setEVMCoinExtendedDenom(eci.ExtendedDenom); err != nil {
		return err
	}
	if err := setDisplayDenom(eci.DisplayDenom); err != nil {
		return err
	}
	return setEVMCoinDecimals(Decimals(eci.Decimals))
}
```

**File:** x/vm/wrappers/bank.go (L37-53)
```go
// MintAmountToAccount converts the given amount into the evm coin scaling
// the amount to the original decimals, then mints that amount to the provided account.
func (w BankWrapper) MintAmountToAccount(ctx context.Context, recipientAddr sdk.AccAddress, amt *big.Int) error {
	coin := sdk.Coin{Denom: types.GetEVMCoinDenom(), Amount: sdkmath.NewIntFromBigInt(amt)}

	convertedCoin, err := types.ConvertEvmCoinDenomToExtendedDenom(coin)
	if err != nil {
		return errors.Wrap(err, "failed to mint coin to account in bank wrapper")
	}

	coinsToMint := sdk.Coins{convertedCoin}
	if err := w.MintCoins(ctx, types.ModuleName, coinsToMint); err != nil {
		return errors.Wrap(err, "failed to mint coins to account in bank wrapper")
	}

	return w.BankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, recipientAddr, coinsToMint)
}
```

**File:** x/vm/types/scaling.go (L46-54)
```go
// ConvertEvmCoinDenomToExtendedDenom converts the coin's Denom to the extended denom.
// Return an error if the coin denom is not the EVM.
func ConvertEvmCoinDenomToExtendedDenom(coin sdk.Coin) (sdk.Coin, error) {
	if coin.Denom != GetEVMCoinDenom() {
		return sdk.Coin{}, fmt.Errorf("expected coin denom %s, received %s", GetEVMCoinDenom(), coin.Denom)
	}

	return sdk.Coin{Denom: GetEVMCoinExtendedDenom(), Amount: coin.Amount}, nil
}
```
