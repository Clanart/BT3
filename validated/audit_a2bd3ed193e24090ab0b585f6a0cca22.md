### Title
ERC20 precompile `Transfer`/`TransferFrom` bypasses the governance-controlled token-pair pause (`ToggleConversion`/`pair.Enabled`) - (File: `precompiles/erc20/tx.go`)

### Summary
The `x/erc20` module exposes a governance "pause" mechanism (`MsgToggleConversion` → `pair.Enabled`) that is meant to stop conversions/transfers involving a registered token pair. This flag is explicitly checked in `MintingEnabled` [1](#0-0)  and in the IBC transfer message server no-op path [2](#0-1) , but it is never consulted by the ERC20 dynamic precompile's `Transfer`/`TransferFrom` entrypoints, which instead go straight to a raw bank `Send`. This mirrors the reported SpiritFactory/PancakePair pattern: a pause flag exists on one contract/module, but the actual value-moving function ignores it and can be called directly to bypass it.

### Finding Description
`GetERC20PrecompileInstance` only checks whether the address is a registered native/dynamic precompile (`IsNativePrecompileAvailable`/`IsDynamicPrecompileAvailable`), then calls `InstantiateERC20Precompile`, which only checks that a `TokenPair` exists for the address — it never checks `pair.Enabled`: [3](#0-2) [4](#0-3) 

The precompile's `Transfer`/`TransferFrom` implementation performs the transfer as a raw bank `MsgSend` (and allowance updates for `transferFrom`), with no reference anywhere to `tokenPair.Enabled` or `IsERC20Enabled`: [5](#0-4) 

Confirmed by grep: `pair.Enabled` is checked in `x/erc20/keeper/mint.go`, `x/erc20/keeper/ibc_callbacks.go`, `x/erc20/keeper/proposals.go`, and `x/ibc/transfer/keeper/msg_server.go`, but not anywhere under `precompiles/erc20/`.

By contrast, other flows that move value for a registered pair explicitly gate on `pair.Enabled`/`IsERC20Enabled`:
- `MintingEnabled` (used for `ConvertERC20`/`ConvertCoin`) [6](#0-5) 
- IBC `OnRecvPacket` no-ops if `IsERC20Enabled` is false [7](#0-6) 
- Native ICS20 `MsgTransfer` no-ops to a regular transfer if the pair is disabled [2](#0-1) 

`ToggleConversion` itself is governance-gated (requires the `gov` module authority and also requires `IsERC20Enabled` globally) [8](#0-7) , and simply flips `pair.Enabled`: [9](#0-8) 

This means an operator relying on `ToggleConversion` to halt activity for a compromised/misbehaving/frozen token pair (e.g., in response to an incident, a bug in the underlying coin, or a sanctioned/blocked-list update) will find that any unprivileged EVM user can still call `transfer`/`transferFrom` directly on the ERC20 precompile address for that pair and move the underlying bank coin, exactly as the PancakePair bypass allowed swaps to proceed despite `SpiritFactory`'s pause.

### Impact Explanation
This does not implement a Critical asset-representation break (mint/burn duplication) by itself — the bank `Send` still respects supply invariants and (indirectly) `IsSendEnabledCoin` via the standard bank msg server. However, it constitutes an unauthorized bypass of an intended value-movement freeze/lock mechanism for a specific token pair: governance disables conversion/transfer for a pair (e.g. to halt an incident, freeze a compromised asset, or block movement while remediating an accounting bug), but the ERC20 precompile transfer path silently ignores that decision and permits an unprivileged user to keep transferring the underlying bank coin through the precompile. If `pair.Enabled=false` is the chain's only lever to freeze movement of a particular token-pair-backed asset (which is exactly its documented purpose, see `ToggleConversion` for enabling/disabling a token pair conversion), this is a "broken invariant that allows continued extraction/movement of funds meant to be frozen" — matching the "permanent freezing/locking is bypassed, allowing unauthorized extraction" class of impact, but scoped strictly to that intended freeze, not to unrestricted new-value creation.

### Likelihood Explanation
High likelihood of being triggerable: any unprivileged EVM account can call `transfer`/`transferFrom` on the token-pair's ERC20 precompile address at any time, including after a `ToggleConversion` disables the pair. No special permissions or race conditions are required — the precompile instantiation path (`GetERC20PrecompileInstance`/`InstantiateERC20Precompile`) simply never reads `pair.Enabled`.

### Recommendation
Add an explicit `pair.Enabled` (and/or `IsERC20Enabled(ctx)`) check inside `precompiles/erc20/tx.go`'s `transfer` function (or in `InstantiateERC20Precompile`/`GetERC20PrecompileInstance`) so that when a token pair is disabled via governance `ToggleConversion`, the ERC20 precompile's `Transfer`/`TransferFrom` (and any other value-moving methods) revert, consistent with the checks already performed in `MintingEnabled` and the IBC transfer/receive paths.

### Proof of Concept
1. Register an ERC20 token pair (native ERC20 or Cosmos coin) via `RegisterERC20`, obtaining a dynamic/native precompile at `pair.GetERC20Contract()`.
2. Governance submits and passes `MsgToggleConversion` for that pair, setting `pair.Enabled = false` [10](#0-9) .
3. An unprivileged EVM account (any holder of the underlying bank coin) sends a transaction calling `transfer(to, amount)` directly on the token pair's precompile address.
4. `GetPrecompileInstance` finds the dynamic/native precompile (pair-existence check only) [3](#0-2) , `Precompile.Transfer` runs, and `transfer()` executes a bank `MsgSend` unconditionally [11](#0-10)  — the transfer succeeds despite the pair being disabled, contrary to the behavior enforced for `ConvertERC20`/`ConvertCoin` and IBC transfer/receive of the same pair.

### Citations

**File:** x/erc20/keeper/mint.go (L18-47)
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

	if !pair.Enabled {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrERC20TokenPairDisabled, "minting token '%s' is not enabled by governance", token,
		)
	}
```

**File:** x/ibc/transfer/keeper/msg_server.go (L54-58)
```go
	pair, _ := k.erc20Keeper.GetTokenPair(ctx, pairID)
	if !pair.Enabled {
		// no-op: pair is not enabled so we can proceed with regular transfer
		return k.Keeper.Transfer(ctx, msg)
	}
```

**File:** x/erc20/keeper/precompiles.go (L28-46)
```go
// GetERC20PrecompileInstance returns the precompile instance for the given address.
func (k Keeper) GetERC20PrecompileInstance(
	ctx sdk.Context,
	address common.Address,
) (contract vm.PrecompiledContract, found bool, err error) {
	isNative := k.IsNativePrecompileAvailable(ctx, address)
	isDynamic := k.IsDynamicPrecompileAvailable(ctx, address)

	if available := isNative || isDynamic; !available {
		return nil, false, nil
	}

	precompile, err := k.InstantiateERC20Precompile(ctx, address, isNative)
	if err != nil {
		return nil, false, errorsmod.Wrapf(err, "precompiled contract not initialized: %s", address.String())
	}

	return precompile, true, nil
}
```

**File:** x/erc20/keeper/precompiles.go (L48-69)
```go
// InstantiateERC20Precompile returns an ERC20 precompile instance for the given
// contract address.
// If the `hasWrappedMethods` boolean is true, the ERC20 instance returned
// exposes methods for `withdraw` and `deposit` as it is common for wrapped tokens.
func (k Keeper) InstantiateERC20Precompile(ctx sdk.Context, contractAddr common.Address, hasWrappedMethods bool) (vm.PrecompiledContract, error) {
	address := contractAddr.String()
	// check if the precompile is an ERC20 contract
	id := k.GetTokenPairID(ctx, address)
	if len(id) == 0 {
		return nil, fmt.Errorf("precompile id not found: %s", address)
	}
	pair, ok := k.GetTokenPair(ctx, id)
	if !ok {
		return nil, fmt.Errorf("token pair not found: %s", address)
	}

	if hasWrappedMethods {
		return werc20.NewPrecompile(pair, k.bankKeeper, k, *k.transferKeeper), nil
	}

	return erc20.NewPrecompile(pair, k.bankKeeper, k, *k.transferKeeper), nil
}
```

**File:** precompiles/erc20/tx.go (L63-116)
```go
// transfer is a common function that handles transfers for the ERC-20 Transfer
// and TransferFrom methods. It executes a bank Send message. If the spender isn't
// the sender of the transfer, it checks the allowance and updates it accordingly.
// transfer is a common function that handles transfers for the ERC-20 Transfer
// and TransferFrom methods. It executes a bank Send message. If the spender isn't
// the sender of the transfer, it checks the allowance and updates it accordingly.
func (p *Precompile) transfer(
	ctx sdk.Context,
	contract *vm.Contract,
	stateDB vm.StateDB,
	method *abi.Method,
	from, to common.Address,
	amount *big.Int,
) (data []byte, err error) {
	coins := sdk.Coins{{Denom: p.tokenPair.Denom, Amount: math.NewIntFromBigInt(amount)}}

	msg := banktypes.NewMsgSend(from.Bytes(), to.Bytes(), coins)

	if err = msg.Amount.Validate(); err != nil {
		return nil, err
	}

	isTransferFrom := method.Name == TransferFromMethod
	spenderAddr := contract.Caller()
	newAllowance := big.NewInt(0)

	if isTransferFrom {
		prevAllowance, err := p.erc20Keeper.GetAllowance(ctx, p.Address(), from, spenderAddr)
		if err != nil {
			return nil, ConvertErrToERC20Error(err)
		}

		newAllowance = new(big.Int).Sub(prevAllowance, amount)
		if newAllowance.Sign() < 0 {
			return nil, ErrInsufficientAllowance
		}

		if newAllowance.Sign() == 0 {
			// If the new allowance is 0, we need to delete it from the store.
			err = p.erc20Keeper.DeleteAllowance(ctx, p.Address(), from, spenderAddr)
		} else {
			// If the new allowance is not 0, we need to set it in the store.
			err = p.erc20Keeper.SetAllowance(ctx, p.Address(), from, spenderAddr, newAllowance)
		}
		if err != nil {
			return nil, ConvertErrToERC20Error(err)
		}
	}

	msgSrv := NewMsgServerImpl(p.BankKeeper)
	if err = msgSrv.Send(ctx, msg); err != nil {
		// This should return an error to avoid the contract from being executed and an event being emitted
		return nil, ConvertErrToERC20Error(err)
	}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L40-43)
```go
	// If ERC20 module is disabled no-op
	if !k.IsERC20Enabled(ctx) {
		return ack
	}
```

**File:** x/erc20/keeper/msg_server.go (L368-393)
```go
func (k *Keeper) ToggleConversion(goCtx context.Context, req *types.MsgToggleConversion) (*types.MsgToggleConversionResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("toggle conversion is currently disabled by governance")
	}

	if err := k.validateAuthority(req.Authority); err != nil {
		return nil, err
	}

	pair, err := k.toggleConversion(ctx, req.Token)
	if err != nil {
		return nil, err
	}

	ctx.EventManager().EmitEvent(
		sdk.NewEvent(
			types.EventTypeToggleTokenConversion,
			sdk.NewAttribute(types.AttributeKeyCosmosCoin, pair.Denom),
			sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
		),
	)

	return &types.MsgToggleConversionResponse{}, nil
}
```

**File:** x/erc20/keeper/proposals.go (L116-138)
```go
// ToggleConversion toggles conversion for a given token pair
func (k Keeper) toggleConversion(
	ctx sdk.Context,
	token string,
) (types.TokenPair, error) {
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

	pair.Enabled = !pair.Enabled
	k.SetTokenPair(ctx, pair)
	return pair, nil
}
```
