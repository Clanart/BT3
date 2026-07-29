### Title
Token pair deletion on selfdestructed native-ERC20 contract permanently strands escrowed conversion funds - ([File: x/erc20/keeper/msg_server.go])

### Summary
`ConvertERC20`/`ConvertCoin` silently delete a `TokenPair` record whenever the underlying native ERC20 contract has no code (e.g. after `selfdestruct`), without any accounting for coins already minted against tokens escrowed in the module account, mirroring the Derby `blacklistProtocol()` pattern of removing a fund-bearing linkage with no claim/settlement step.

### Finding Description
For `OWNER_EXTERNAL` (native ERC20) token pairs, users escrow their ERC20 tokens into `types.ModuleAddress` and receive minted bank coins via `convertERC20IntoCoinsForNativeToken` [1](#0-0) , and can later burn those coins to unescrow the ERC20 tokens via `ConvertCoinNativeERC20` [2](#0-1) . Both `ConvertERC20` and `ConvertCoin` first check whether the paired contract still has code, and if not, immediately call `DeleteTokenPair` and return without any settlement of outstanding escrowed balances: [3](#0-2) [4](#0-3) 

`DeleteTokenPair` wipes the denom map, the ERC20 map, and the pair record entirely [5](#0-4) . Once this happens, `GetTokenPairID`/`GetTokenPair` can no longer resolve the denom or the ERC20 address [6](#0-5) , so `MintingEnabled` (used by both `ConvertCoin` and `ConvertERC20`) will always fail with `ErrTokenPairNotFound` for that denom going forward [7](#0-6) . Any bank-coin balance still held by users that represents ERC20 escrowed prior to the deletion becomes permanently non-redeemable — there is no mechanism analogous to the report's recommended "claim before blacklist" step to settle outstanding balances before severing the pair.

This is the same structural flaw as the Derby `blacklistProtocol()` bug: a state-mutating action (there: guardian blacklisting a protocol; here: contract-code-absence check triggered by an ordinary `ConvertERC20`/`ConvertCoin` call from any unprivileged user) unilaterally zeroes out/removes the linkage between a fund-bearing coin and its backing asset with no reconciliation step, freezing whatever value was already escrowed under that mapping.

### Impact Explanation
Coins minted against previously-escrowed ERC20 tokens become permanently unredeemable once the pair record is deleted, since the escrow-to-coin bidirectional link (`ConvertCoinNativeERC20`/`convertERC20IntoCoinsForNativeToken`) can no longer resolve the pair. This is a permanent freezing of user-spendable value tied to a token-pair-backed balance, matching the Critical impact class of "permanent freezing... of user funds... or token-pair-backed balances."

### Likelihood Explanation
The trigger condition (`acc == nil || !acc.HasCodeHash()`) is checked on essentially every `ConvertERC20`/`ConvertCoin` call and can be hit by any unprivileged account, without governance, as soon as the underlying ERC20 contract's code is removed (e.g., a contract that self-destructs, or is destroyed by its own deployer for any reason after being registered as a pair) — this requires no privileged actor and can be triggered by any user submitting the conversion message.

### Recommendation
Before deleting a token pair whose contract lost its code, reconcile any outstanding coin supply for that denom against the module's escrowed balance and provide a settlement/redemption path (e.g. allow burning of remaining coin supply against a pro-rata claim, or block deletion until a graceful winddown occurs) rather than unconditionally discarding the pair state, consistent with the report's recommendation to perform claiming/settlement before removing the linkage.

### Proof of Concept
Could not be fully constructed/verified from the available indexed code (e.g., I could not confirm from the index whether existing `total coin supply` invariant checks or module-level safeguards intercept this scenario, nor view the complete `test_msg_server.go` selfdestruct test cases that reference this code path). A definitive PoC would require:
1. Registering a native ERC20 token pair via `RegisterERC20`.
2. Escrowing tokens via `ConvertERC20` to mint bank coins.
3. Self-destructing the underlying ERC20 contract via a normal EVM transaction.
4. Calling `ConvertERC20` or `ConvertCoin` again to trigger `DeleteTokenPair`.
5. Observing that the previously-minted bank coin balance can no longer be redeemed for the escrowed ERC20 (now inaccessible) tokens.

Given the incomplete verification of surrounding invariant guards (index coverage limits prevented full inspection of `tests/integration/x/erc20/test_msg_server.go` and related invariant-checking code), I recommend starting a full Devin session with repository access to confirm whether any additional safeguard exists before treating this as a confirmed, unmitigated Critical finding.

### Citations

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

**File:** x/erc20/keeper/msg_server.go (L63-77)
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
```

**File:** x/erc20/keeper/msg_server.go (L209-220)
```go
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

**File:** x/erc20/keeper/msg_server.go (L237-266)
```go
func (k Keeper) ConvertCoinNativeERC20(
	ctx sdk.Context,
	pair types.TokenPair,
	amount math.Int,
	receiver common.Address,
	sender sdk.AccAddress,
) error {
	if !amount.IsPositive() {
		return sdkerrors.Wrap(types.ErrNegativeToken, "converted coin amount must be positive")
	}

	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()

	balanceToken := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceToken == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	// Escrow Coins on module account
	coins := sdk.Coins{{Denom: pair.Denom, Amount: amount}}
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, coins); err != nil {
		return sdkerrors.Wrap(err, "failed to escrow coins")
	}

	// Unescrow Tokens and send to receiver
	res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, true, nil, "transfer", receiver, amount.BigInt())
	if err != nil {
		return err
	}
```

**File:** x/erc20/keeper/token_pairs.go (L77-100)
```go
func (k Keeper) GetTokenPairID(ctx sdk.Context, token string) []byte {
	if common.IsHexAddress(token) {
		addr := common.HexToAddress(token)
		return k.GetERC20Map(ctx, addr)
	}
	return k.GetDenomMap(ctx, token)
}

// GetTokenPair gets a registered token pair from the identifier.
func (k Keeper) GetTokenPair(ctx sdk.Context, id []byte) (types.TokenPair, bool) {
	if id == nil {
		return types.TokenPair{}, false
	}

	store := prefix.NewStore(ctx.KVStore(k.storeKey), types.KeyPrefixTokenPair)
	var tokenPair types.TokenPair
	bz := store.Get(id)
	if len(bz) == 0 {
		return types.TokenPair{}, false
	}

	k.cdc.MustUnmarshal(bz, &tokenPair)
	return tokenPair, true
}
```

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

**File:** x/erc20/keeper/mint.go (L18-41)
```go
func (k Keeper) MintingEnabled(
	ctx sdk.Context,
	receiver sdk.AccAddress,
	token string,
) (types.TokenPair, error) {
	if !k.IsERC20Enabled(ctx) {
		return types.TokenPair{}, errorsmod.Wrap(
			types.ErrERC20Disabled, "module is currently disabled by governance",
		)
	}

	id := k.GetTokenPairID(ctx, token)
	if len(id) == 0 {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered by id", token,
		)
	}

	pair, found := k.GetTokenPair(ctx, id)
	if !found {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered", token,
		)
	}
```
