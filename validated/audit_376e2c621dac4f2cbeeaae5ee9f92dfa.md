Confirmed: `DeleteTokenPair` at [1](#0-0)  never removes the bank denom metadata that was created by `CreateCoinMetadata`, and `CreateCoinMetadata` unconditionally rejects re-creation if that metadata still exists [2](#0-1) .

### Title
Self-destructed native ERC20 token pairs permanently strand converted bank coins and can never be re-registered - (File: x/erc20/keeper/token_pairs.go, x/erc20/keeper/proposals.go, x/erc20/keeper/msg_server.go)

### Summary
This is the same bug class as the Sherlock `SingleSidedLiquidityVault` report: a "global" accounting record (there: `accumulatedRewardsPerShare`; here: the bank `DenomMetadata`/`TokenPair` mapping) is deleted on removal, while a dependent, per-user accounting record (there: individual reward trackers; here: already-minted bank `Coin` balances of that denom held by users) is **not** cleaned up or reconciled. Re-adding the entity later collides with the still-existing per-user state and permanently breaks the invariant, exactly mirroring the underflow-on-re-add pattern from the original report.

### Finding Description
When a native ERC20 token is registered via `RegisterERC20`, `registerERC20` calls `CreateCoinMetadata`, which creates a bank `DenomMetadata` for the deterministic denom `types.CreateDenom(contract)` [3](#0-2) . Users can then convert ERC20 balances into these bank coins via `ConvertERC20`/`convertERC20IntoCoinsForNativeToken`, which escrows the ERC20 tokens on the module account inside the contract and mints the corresponding bank coins to the receiver [4](#0-3) .

Both `ConvertERC20` and `ConvertCoin` contain "auto-cleanup" logic: if the underlying ERC20 contract has been self-destructed (`acc == nil || !acc.HasCodeHash()`), the token pair is deleted via `k.DeleteTokenPair(ctx, pair)` [5](#0-4) [6](#0-5) .

`DeleteTokenPair` only removes the `TokenPair` record, the ERC20→ID map, the denom→ID map, and allowances — it never deletes the bank `DenomMetadata` entry created earlier [1](#0-0) .

Consequently:
1. **Permanent registration DoS for the address/denom**: If a new contract is later deployed at the same address (e.g., via `CREATE2` redeploy after `SELFDESTRUCT`, which is a normal, unprivileged EVM pattern) and a user calls `RegisterERC20` again, `CreateCoinMetadata` finds the stale `DenomMetadata` still present and unconditionally returns `ErrInternalTokenPair "denom metadata already registered"` [7](#0-6) , so the pair (and therefore that denom) can never be registered again — mirroring the Sherlock report's "reward tokens can never be added again."
2. **Permanent freezing of already-converted user funds**: Any bank coins of that denom that were minted to users via prior `ConvertERC20` calls remain in circulation, but their backing ERC20 collateral (escrowed at `types.ModuleAddress` inside the now-destroyed contract) is unrecoverable, and there is no token pair through which those coins could ever be redeemed back into ERC20 tokens (`ConvertCoin` requires `MintingEnabled`, which requires an existing, enabled `TokenPair` — impossible once deleted and impossible to re-create due to (1)). This permanently locks the ERC20-side value that legitimately backed those coins, with no governance or user-level recovery path.

### Impact Explanation
This satisfies the "Critical permanent freezing … of spendable user value … across … token-pair-backed balances" allowed-impact category: converted value becomes permanently unredeemable, and the token/denom is permanently unusable for any future registration, with no existing guard reconstituting or clearing the stale bank metadata on deletion.

### Likelihood Explanation
Triggering requires only: (a) a native ERC20 contract that self-destructs (achievable by any contract owner/deployer under normal, unprivileged usage — self-destructing contracts is a standard EVM operation, not a privileged action), and (b) any user subsequently calling `ConvertERC20`/`ConvertCoin` against that pair (which triggers the auto-delete) or `RegisterERC20` for a redeployed contract at the same address. No validator, relayer, or governance involvement is needed, making this reachable by an ordinary user through standard transaction flows.

### Recommendation
When `DeleteTokenPair` is invoked (particularly from the self-destruct auto-cleanup path), also remove/reset the associated bank `DenomMetadata`, or otherwise version the denom's registration/backing-state so a fresh `TokenPair` can be created without colliding with orphaned metadata. Additionally, consider requiring the total supply of the coin denom to be verified/burned or migrated before allowing deletion, so that no unredeemable bank coins remain in circulation once their ERC20 backing is destroyed.

### Proof of Concept
1. Deploy `ERC20MinterBurnerDecimals` contract `C`, register it via `MsgRegisterERC20` → creates `TokenPair` and bank `DenomMetadata` for `denom = CreateDenom(C)`.
2. Mint tokens to user `A`, call `ConvertERC20` to convert `A`'s ERC20 balance into bank coins of `denom` — this escrows tokens at `types.ModuleAddress` inside `C` and mints `denom` coins to `A` (see `convertERC20IntoCoinsForNativeToken`, [8](#0-7) ).
3. Contract `C` self-destructs (e.g., admin calls a `selfdestruct` function, or via `CREATE2`+destroy pattern).
4. Any subsequent `ConvertERC20`/`ConvertCoin` call referencing pair `C` detects `!acc.HasCodeHash()` and calls `k.DeleteTokenPair(ctx, pair)`, removing the `TokenPair`/ERC20 map/denom map/allowances but leaving the bank `DenomMetadata` for `denom` in place.
5. `A` still holds spendable `denom` coins but there is no `TokenPair` to redeem them through, and `MintingEnabled`/`ConvertCoin` will fail with `ErrTokenPairNotFound`, so the value is permanently stranded.
6. If anyone redeploys a contract to the same address `C` (via `CREATE2`) and calls `MsgRegisterERC20{C}`, `registerERC20` → `CreateCoinMetadata` fails with `ErrInternalTokenPair("denom metadata already registered")` because the stale metadata from step 1 was never deleted — the address/denom can never be registered again.

### Citations

**File:** x/erc20/keeper/token_pairs.go (L110-117)
```go
// DeleteTokenPair removes a token pair.
func (k Keeper) DeleteTokenPair(ctx sdk.Context, tokenPair types.TokenPair) {
	id := tokenPair.GetID()
	k.deleteTokenPair(ctx, id)
	k.deleteERC20Map(ctx, tokenPair.GetERC20Contract())
	k.deleteDenomMap(ctx, tokenPair.Denom)
	k.deleteAllowances(ctx, tokenPair.GetERC20Contract())
}
```

**File:** x/erc20/keeper/proposals.go (L44-113)
```go
// CreateCoinMetadata generates the metadata to represent the ERC20 token on
// evmos.
func (k Keeper) CreateCoinMetadata(
	ctx sdk.Context,
	contract common.Address,
) (*banktypes.Metadata, error) {
	strContract := contract.String()

	erc20Data, err := k.QueryERC20(ctx, contract)
	if err != nil {
		return nil, err
	}

	// Check if metadata already exists
	_, found := k.bankKeeper.GetDenomMetaData(ctx, types.CreateDenom(strContract))
	if found {
		return nil, errorsmod.Wrap(
			types.ErrInternalTokenPair, "denom metadata already registered",
		)
	}

	if k.IsDenomRegistered(ctx, types.CreateDenom(strContract)) {
		return nil, errorsmod.Wrapf(
			types.ErrInternalTokenPair, "coin denomination already registered: %s", erc20Data.Name,
		)
	}

	// base denomination
	base := types.CreateDenom(strContract)

	// create a bank denom metadata based on the ERC20 token ABI details
	// metadata name is should always be the contract since it's the key
	// to the bank store
	metadata := banktypes.Metadata{
		Description: types.CreateDenomDescription(strContract),
		Base:        base,
		// NOTE: Denom units MUST be increasing
		DenomUnits: []*banktypes.DenomUnit{
			{
				Denom:    base,
				Exponent: 0,
			},
		},
		Name:    types.CreateDenom(strContract),
		Symbol:  erc20Data.Symbol,
		Display: base,
	}

	// only append metadata if decimals > 0, otherwise validation fails
	if erc20Data.Decimals > 0 {
		nameSanitized := types.SanitizeERC20Name(erc20Data.Name)
		metadata.DenomUnits = append(
			metadata.DenomUnits,
			&banktypes.DenomUnit{
				Denom:    nameSanitized,
				Exponent: uint32(erc20Data.Decimals), //#nosec G115 -- int overflow is not a concern here
			},
		)
		metadata.Display = nameSanitized
	}

	if err := metadata.Validate(); err != nil {
		return nil, errorsmod.Wrapf(
			err, "ERC20 token data is invalid for contract %s", strContract,
		)
	}

	k.bankKeeper.SetDenomMetaData(ctx, metadata)

	return &metadata, nil
```

**File:** x/erc20/keeper/msg_server.go (L42-53)
```go
	if pair.IsNativeERC20() {
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```

**File:** x/erc20/keeper/msg_server.go (L63-152)
```go
// convertERC20IntoCoinsForNativeToken handles the erc20 conversion for a native erc20 token
// pair:
//   - escrow tokens on module account
//   - mint coins on bank module
//   - send minted coins to the receiver
//   - check if coin balance increased by amount
//   - check if token balance decreased by amount
//   - check for unexpected `Approval` event in logs
func (k Keeper) convertERC20IntoCoinsForNativeToken(
	ctx sdk.Context,
	pair types.TokenPair,
	msg *types.MsgConvertERC20,
	receiver sdk.AccAddress,
	sender common.Address,
) (*types.MsgConvertERC20Response, error) {
	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()
	balanceCoin := k.bankKeeper.GetBalance(ctx, receiver, pair.Denom)
	balanceToken := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceToken == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	// Escrow tokens on module account
	transferData, err := erc20.Pack("transfer", types.ModuleAddress, msg.Amount.BigInt())
	if err != nil {
		return nil, err
	}

	res, err := k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)
	if err != nil {
		return nil, err
	}

	// Check evm call response
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return nil, err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return nil, err
		}
		if !unpackedRet.Value {
			return nil, sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute transfer")
		}
	}

	// Check expected escrow balance after transfer execution
	// NOTE: coin fields already validated in the ValidateBasic() of the message
	coins := sdk.Coins{sdk.Coin{Denom: pair.Denom, Amount: msg.Amount}}
	tokens := coins[0].Amount.BigInt()
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceTokenAfter == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}

	// Mint coins
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return nil, err
	}

	// Send minted coins to the receiver
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiver, coins); err != nil {
		return nil, err
	}

	// Check expected receiver balance after transfer
	balanceCoinAfter := k.bankKeeper.GetBalance(ctx, receiver, pair.Denom)
	expCoin := balanceCoin.Add(coins[0])

	if ok := balanceCoinAfter.Equal(expCoin); !ok {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid coin balance - expected: %v, actual: %v",
			expCoin, balanceCoinAfter,
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L207-220)
```go
	// Check ownership and execute conversion
	switch {
	case pair.IsNativeERC20():
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```
