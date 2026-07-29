## Analysis

The relevant flow is: `Keeper.RegisterERC20` (permissionless) → `registerERC20` creates a `TokenPair` keyed by the ERC20 contract **address** only [1](#0-0) , and later `ConvertCoin`/`ConvertERC20` re-resolve that same `TokenPair` and call into whatever bytecode currently lives at that address via `k.evmKeeper.CallEVM(...)`/`k.BalanceOf(...)` [2](#0-1) .

Both entrypoints do contain a self-destruct guard:

```go
acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
if acc == nil || !acc.HasCodeHash() {
    k.DeleteTokenPair(ctx, pair)
    ...
    return nil, nil
}
``` [3](#0-2) [4](#0-3) 

This check only verifies that *some* code currently exists at the address — it never records or compares the codehash that was present when the pair was registered. If the token owner self-destructs and redeploys (via `CREATE2`) different bytecode at the same address, `acc.HasCodeHash()` is true again and the module proceeds with the normal conversion path against the new, attacker-controlled logic — none of the code in `dynamic_precompiles.go`, `token_pairs.go`, or `mint.go` records/verifies a codehash for externally-owned (`OWNER_EXTERNAL`) token pairs to detect this substitution [5](#0-4) .

Critically, the "balance invariance" checks in `ConvertCoinNativeERC20` and `convertERC20IntoCoinsForNativeToken` only compare `BalanceOf` before/after calls made against the *same* (now attacker-controlled) contract [6](#0-5) [7](#0-6) . A malicious redeployed contract can trivially report whatever `balanceOf`/`transfer` results satisfy this self-referential check, since the check has no independent ground truth. Meanwhile `ConvertCoinNativeERC20` performs a real `BurnCoins` of the user's native coin [8](#0-7) , so the coin destruction is genuine even though what the user receives back is worthless/fake token accounting.

### Title
Self-destruct + CREATE2 metamorphic redeploy at a registered ERC20 address bypasses the stale-contract guard and lets a malicious token owner steal converted native coin value - (`x/erc20/keeper/msg_server.go`)

### Summary
`RegisterERC20` binds a `TokenPair` to an ERC20 contract by address only, with no persisted codehash for `OWNER_EXTERNAL` pairs. `ConvertCoin`/`ConvertERC20` only guard against a *currently self-destructed* contract (`acc == nil || !acc.HasCodeHash()`), not against a contract that has been self-destructed and then **redeployed with different code at the same address** via `CREATE2`. An attacker who deployed the original (legitimate-looking) token via a `CREATE2` factory can, after users trust it and use `MsgConvertCoin` against it, self-destruct and redeploy malicious logic. Subsequent `ConvertCoin` calls pass the module's self-referential balance-invariance checks (which only query the new, attacker-controlled contract) while still executing a real `BurnCoins` of the user's native coin.

### Impact Explanation
Users who call `MsgConvertCoin` against a token pair whose backing contract has been swapped will have real, spendable native coin permanently burned (`k.bankKeeper.BurnCoins`) [8](#0-7)  in exchange for tokens on a contract whose `balanceOf`/`transfer` semantics are entirely attacker-controlled and can be worthless or non-transferable. This is an irreversible loss of value for an unprivileged victim, matching the "theft/permanent loss of token-pair-backed balances" impact category.

### Likelihood Explanation
Requires the attacker to be the original deployer of the registered ERC20 (deployed through a `CREATE2` factory so the address is reproducible), for `PermissionlessRegistration` to be enabled (or governance to have registered it), and for victims to use `MsgConvertCoin` against that specific pair after the swap — i.e., it is scoped to whichever token pair the attacker themselves seeded, not an attack on arbitrary/unrelated token pairs. This bounds likelihood but the mechanics do not require any validator, admin, or governance compromise — only ordinary contract-deployment and message-submission capability.

### Recommendation
Persist the codehash observed at `registerERC20` time for `OWNER_EXTERNAL` pairs and re-verify it (not just "does code exist") on every `ConvertCoin`/`ConvertERC20` call, deleting/disabling the pair (as is already done for full self-destruction) whenever the on-chain codehash no longer matches the registered one.

### Proof of Concept
1. Deploy an ERC20 via a `CREATE2` factory contract, register it with `MsgRegisterERC20` (permissionless).
2. Have victim(s) call `MsgConvertCoin` to convert native coin into the ERC20, building up trust/usage.
3. Self-destruct the ERC20 contract, then redeploy different bytecode at the same address via the same `CREATE2` factory/salt, implementing `balanceOf`/`transfer` to always satisfy the module's invariance checks without real value transfer.
4. Victim calls `MsgConvertCoin` again (or a new victim does): `acc.HasCodeHash()` is true so the stale-pair deletion branch in `ConvertCoin`/`ConvertCoinNativeERC20` is skipped [9](#0-8) ; native coin is burned via `BurnCoins` while the "received" ERC20 balance is fabricated by the attacker's new contract.

### Citations

**File:** x/erc20/keeper/proposals.go (L18-41)
```go
func (k Keeper) registerERC20(
	ctx sdk.Context,
	contract common.Address,
) (*types.TokenPair, error) {
	// Check if ERC20 is already registered
	if k.IsERC20Registered(ctx, contract) {
		return nil, errorsmod.Wrapf(
			types.ErrTokenPairAlreadyExists, "token ERC20 contract already registered: %s", contract.String(),
		)
	}

	metadata, err := k.CreateCoinMetadata(ctx, contract)
	if err != nil {
		return nil, errorsmod.Wrap(
			err, "failed to create wrapped coin denom metadata for ERC20",
		)
	}

	pair := types.NewTokenPair(contract, metadata.Name, types.OWNER_EXTERNAL)
	err = k.SetToken(ctx, pair)
	if err != nil {
		return nil, err
	}
	return &pair, nil
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

**File:** x/erc20/keeper/msg_server.go (L113-130)
```go
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
```

**File:** x/erc20/keeper/msg_server.go (L192-228)
```go
func (k Keeper) ConvertCoin(
	goCtx context.Context,
	msg *types.MsgConvertCoin,
) (*types.MsgConvertCoinResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// Error checked during msg validation
	sender := sdk.MustAccAddressFromBech32(msg.Sender)
	receiver := common.HexToAddress(msg.Receiver)

	pair, err := k.MintingEnabled(ctx, receiver.Bytes(), msg.Coin.Denom)
	if err != nil {
		return nil, err
	}

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

		return nil, k.ConvertCoinNativeERC20(ctx, pair, msg.Coin.Amount, receiver, sender)
	case pair.IsNativeCoin():
		return nil, types.ErrNativeConversionDisabled
	}

	return nil, types.ErrUndefinedOwner
}
```

**File:** x/erc20/keeper/msg_server.go (L284-297)
```go
	// Check expected Receiver balance after transfer execution
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceTokenAfter == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	exp := big.NewInt(0).Add(balanceToken, amount.BigInt())

	if r := balanceTokenAfter.Cmp(exp); r != 0 {
		return sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v", exp, balanceTokenAfter,
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L299-303)
```go
	// Burn escrowed Coins
	err = k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins)
	if err != nil {
		return sdkerrors.Wrap(err, "failed to burn coins")
	}
```

**File:** x/erc20/keeper/dynamic_precompiles.go (L33-63)
```go
// RegisterERC20CodeHash sets the codehash for the erc20 precompile account
// if the bytecode for the erc20 codehash does not exists, it stores it.
func (k Keeper) RegisterERC20CodeHash(ctx sdk.Context, erc20Addr common.Address) error {
	var (
		// bytecode and codeHash is the same for all IBC coins
		// cause they're all using the same contract
		bytecode = common.FromHex(types.Erc20Bytecode)
		codeHash = crypto.Keccak256(bytecode)
	)
	// check if code was already stored
	code := k.evmKeeper.GetCode(ctx, common.Hash(codeHash))
	if len(code) == 0 {
		k.evmKeeper.SetCode(ctx, codeHash, bytecode)
	}

	var (
		nonce   uint64
		balance = common.U2560
	)
	// keep balance and nonce if account exists
	if acc := k.evmKeeper.GetAccount(ctx, erc20Addr); acc != nil {
		nonce = acc.Nonce
		balance = acc.Balance
	}

	return k.evmKeeper.SetAccount(ctx, erc20Addr, statedb.Account{
		CodeHash: codeHash,
		Nonce:    nonce,
		Balance:  balance,
	})
}
```
