This confirms the analog: `IsSendEnabledCoins` and `IsInDenomAllowList`/`BlockedAddr` checks are enforced only at the `MsgServer.Send` / `MsgMultiSend` layer [1](#0-0) , not inside `BaseSendKeeper.SendCoins` itself. The bank precompile's `send` function calls `p.bankKeeper.SendCoins` directly, bypassing these checks entirely.

### Title
Bank precompile `send`/`sendNative` bypass `SendEnabled` and denom allow-list validation - (File: precompiles/bank/bank.go, precompiles/bank/legacy/v562/bank.go, v580/bank.go, v600/bank.go, v555/bank.go)

### Summary
The GMX report's root cause is that state-changing entry points (`Swap`, `CreateAdl`) invoke internal transfer/settlement logic directly without re-checking whether the underlying market is enabled — that check exists only on a different, non-enforced code path. The analogous pattern exists in sei-chain's bank precompile: `SendEnabled` and denom allow-list checks are implemented in `BaseSendKeeper`/`msgServer.Send` but are only invoked from the Cosmos `MsgSend`/`MsgMultiSend` handlers, not from `BaseSendKeeper.SendCoins` itself [2](#0-1) . The EVM bank precompile's `send` executor calls `p.bankKeeper.SendCoins` directly after only validating that the caller is the correct ERC20 pointer for the denom — it never calls `IsSendEnabledCoins` or `IsInDenomAllowList`/`BlockedAddr` [3](#0-2) .

### Finding Description
`msgServer.Send` explicitly checks `k.IsSendEnabledCoins(ctx, msg.Amount...)` and denom allow-list/blocked-address rules before calling `SendCoins` [1](#0-0) . These checks are not enforced inside `BaseSendKeeper.SendCoins`/`IsSendEnabledCoins` is a separate opt-in call [4](#0-3) . The EVM bank precompile (all legacy versions v555–v600 and the current `precompiles/bank`) implements `send`/`sendNative` by calling `p.bankKeeper.SendCoins`/`SendCoinsAndWei` directly, with the only guard being that `caller` matches the registered ERC20 native pointer for the denom [5](#0-4) . It never calls `IsSendEnabledCoins`, `IsInDenomAllowList`, or `BlockedAddr`, so this bypasses the same authority-gating logic that the Cosmos-side `MsgSend` path enforces — structurally identical to the GMX bug where the "enabled" check lived in one code path (order execution) but was missing from another reachable path (`Swap`/`CreateAdl`).

### Impact Explanation
If bank governance disables sending for a denom (`SendEnabled=false`) or a denom/address is placed in a deny/allow list (e.g., for compliance, tokenfactory-controlled denoms, or a paused asset), an EVM user can still move that denom's balance by calling the bank precompile's `send`/`sendNative` through the associated ERC20 pointer contract, completely circumventing the restriction that governance/module intended to enforce. This is a real authorization/fund-movement-control bypass reachable by any EVM caller holding the pointer-associated token, not merely an informational gap.

### Likelihood Explanation
High reachability: any address with an EVM-associated Sei account and a nonzero balance of a denom that has a live ERC20 pointer can call the precompile's `send` function directly; no special privileges are required beyond being the pointer contract caller (which itself is a standard ERC20 `transfer` call routed through the pointer). This makes the analog directly reachable from a single unprivileged EVM transaction.

### Recommendation
Add `bankKeeper.IsSendEnabledCoins(ctx, sdk.NewCoin(denom, amount))` and the same `IsInDenomAllowList`/`BlockedAddr` checks used in `msgServer.Send` into the bank precompile's `send` and `sendNative` executors (and any other precompile/pointer path that calls `SendCoins`/`SendCoinsAndWei` directly), before executing the transfer, mirroring the enforcement that already exists in `x/bank/keeper/msg_server.go`.

### Proof of Concept
1. Governance sets `SendEnabled=false` for denom `X` (or adds an entry to `DenomAllowList` restricting a given address) via bank params. `MsgSend` for `X` now fails via `msgServer.Send`'s `IsSendEnabledCoins` check.
2. An EVM user holding an ERC20 pointer balance for `X` calls the pointer contract's `transfer`, which routes to the bank precompile's `send(sender, receiver, "X", amount)`.
3. `PrecompileExecutor.send` only checks that `caller` is the registered pointer for `X`, then calls `p.bankKeeper.SendCoins` directly — no `IsSendEnabledCoins`/allow-list check is performed, so the transfer succeeds despite `X` being send-disabled [6](#0-5) .

### Citations

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-51)
```go
func (k msgServer) Send(goCtx context.Context, msg *types.MsgSend) (*types.MsgSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	if err := k.IsSendEnabledCoins(ctx, msg.Amount...); err != nil {
		return nil, err
	}

	from, err := sdk.AccAddressFromBech32(msg.FromAddress)
	if err != nil {
		return nil, err
	}
	to, err := sdk.AccAddressFromBech32(msg.ToAddress)
	if err != nil {
		return nil, err
	}

	allowListCache := make(map[string]AllowedAddresses)
	if !k.IsInDenomAllowList(ctx, from, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", msg.FromAddress)
	}

	if k.BlockedAddr(to) || !k.IsInDenomAllowList(ctx, to, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", msg.ToAddress)
	}

	err = k.SendCoins(ctx, from, to, msg.Amount)
```

**File:** sei-cosmos/x/bank/keeper/send.go (L26-52)
```go
// SendKeeper defines a module interface that facilitates the transfer of coins
// between accounts without the possibility of creating coins.
type SendKeeper interface {
	ViewKeeper

	InputOutputCoins(ctx sdk.Context, inputs []types.Input, outputs []types.Output) error
	SendCoins(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error
	SendCoinsWithoutAccCreation(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error
	SendCoinsAndWei(ctx sdk.Context, from sdk.AccAddress, to sdk.AccAddress, amt sdk.Int, wei sdk.Int) error
	SubUnlockedCoins(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Coins, checkNeg bool) error
	AddCoins(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Coins, checkNeg bool) error
	SubWei(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Int) error
	AddWei(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Int) error

	GetParams(ctx sdk.Context) types.Params
	SetParams(ctx sdk.Context, params types.Params)

	IsSendEnabledCoin(ctx sdk.Context, coin sdk.Coin) bool
	IsSendEnabledCoins(ctx sdk.Context, coins ...sdk.Coin) error
	SetDenomAllowList(ctx sdk.Context, denom string, allowList types.AllowList)
	GetDenomAllowList(ctx sdk.Context, denom string) types.AllowList
	IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool

	BlockedAddr(addr sdk.AccAddress) bool
	RegisterRecipientChecker(RecipientChecker)
	CanSendTo(ctx sdk.Context, recipient sdk.AccAddress) bool
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L355-365)
```go
// IsSendEnabledCoins checks the coins provide and returns an ErrSendDisabled if
// any of the coins are not configured for sending.  Returns nil if sending is enabled
// for all provided coin
func (k BaseSendKeeper) IsSendEnabledCoins(ctx sdk.Context, coins ...sdk.Coin) error {
	for _, coin := range coins {
		if !k.IsSendEnabledCoin(ctx, coin) {
			return sdkerrors.Wrapf(types.ErrSendDisabled, "%s transfers are currently disabled", coin.Denom)
		}
	}
	return nil
}
```

**File:** precompiles/bank/legacy/v562/bank.go (L135-173)
```go
func (p PrecompileExecutor) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, error) {
	if readOnly {
		return nil, errors.New("cannot call send from staticcall")
	}
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, err
	}
	denom := args[2].(string)
	if denom == "" {
		return nil, errors.New("invalid denom")
	}
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
	amount := args[3].(*big.Int)
	if amount.Cmp(utils.Big0) == 0 {
		// short circuit
		return method.Outputs.Pack(true)
	}
	senderSeiAddr, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		return nil, err
	}
	receiverSeiAddr, err := p.accAddressFromArg(ctx, args[1])
	if err != nil {
		return nil, err
	}

	if err := p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount)))); err != nil {
		return nil, err
	}

	return method.Outputs.Pack(true)
}
```

**File:** precompiles/bank/legacy/v580/bank.go (L121-159)
```go
func (p PrecompileExecutor) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, error) {
	if readOnly {
		return nil, errors.New("cannot call send from staticcall")
	}
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, err
	}
	denom := args[2].(string)
	if denom == "" {
		return nil, errors.New("invalid denom")
	}
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
	amount := args[3].(*big.Int)
	if amount.Cmp(utils.Big0) == 0 {
		// short circuit
		return method.Outputs.Pack(true)
	}
	senderSeiAddr, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		return nil, err
	}
	receiverSeiAddr, err := p.accAddressFromArg(ctx, args[1])
	if err != nil {
		return nil, err
	}

	if err := p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount)))); err != nil {
		return nil, err
	}

	return method.Outputs.Pack(true)
}
```
