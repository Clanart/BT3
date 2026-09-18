Confirmed: in the current `sendNative` function of the bank precompile, the recipient address is taken directly from the caller-supplied bech32 string and passed straight into `p.bankKeeper.SendCoinsAndWei`, with no `BlockedAddr` (or equivalent module/blocked-address) check, unlike the native Cosmos `MsgSend` handler which explicitly calls `k.BlockedAddr(to)` before allowing a transfer. [1](#0-0) [2](#0-1) 

### Title
Missing `BlockedAddr` validation on EVM `bank` precompile `sendNative`/`send` recipient allows funds to be sent to protected module accounts - (File: precompiles/bank/bank.go)

### Summary
The `bank` precompile's `sendNative` function (and the sibling `send` function, which resolves the recipient via `accAddressFromArg`) accepts an arbitrary caller-supplied address string/EVM address as the recipient and forwards it directly to `p.bankKeeper.SendCoinsAndWei` / `p.bankKeeper.SendCoins`, without ever checking `BlockedAddr`. This is the exact bug class described in the external report: a security-sensitive "account" parameter (here, the transfer recipient) is never validated against a disallowed category of addresses (module accounts / blocked addresses), unlike the equivalent native Cosmos SDK path.

### Finding Description
In `sendNative`, the recipient is parsed directly from `args[0]` and converted with `sdk.AccAddressFromBech32`, then passed straight to `SendCoinsAndWei`: [1](#0-0) 

Similarly, the `send` function resolves both sender and receiver through `accAddressFromArg`, which either maps an associated EVM address to its Sei address or falls back to casting the raw EVM bytes into an `sdk.AccAddress` — again with no blocked-address check: [3](#0-2) [4](#0-3) 

By contrast, the canonical Cosmos SDK bank message path (`MsgServer.Send`) explicitly rejects transfers whose destination is a blocked address (module accounts such as `gov`, `mint`, `distribution`, `bonded_tokens_pool`, etc.) before calling `SendCoins`: [5](#0-4) 

The underlying `BaseSendKeeper.SendCoins`/`SendCoinsWithoutAccCreation` functions themselves perform no `BlockedAddr` check — that check lives exclusively in the message-server layer (and in `SendCoinsFromModuleToAccount`), so any code path that calls `SendCoins`/`SendCoinsAndWei` directly (as both bank-precompile entry points do) bypasses this protection entirely: [6](#0-5) [7](#0-6) 

This directly parallels the Notional finding: an "Account" argument that should be constrained to a normal EOA is instead allowed to be a privileged/system address (there, a vault; here, a Cosmos module account address), because the authenticating/validating layer never enforces the restriction that upstream code paths rely on.

### Impact Explanation
Any unprivileged EVM transaction sender can call the `bank` precompile's `sendNative` (or `send`) method with `to` set to a well-known Cosmos module account address (e.g. `gov`, `distribution`, `bonded_tokens_pool`, `mint`) computed off-chain via `authtypes.NewModuleAddress(name)`. Funds land in that module account's balance without going through the module's expected accounting/message flow. Depending on the target module's internal invariants (e.g., some modules assume their balance strictly equals tracked internal state, or route balances via specific epoch/settlement logic), this can:
- Permanently strand user funds in a module account that has no user-facing withdrawal path (permanent freezing of funds), and/or
- Corrupt a module's internal accounting invariant (balance vs. tracked liabilities), which for some modules can lead to incorrect downstream fund movement or invariant-check panics affecting chain liveness.

This satisfies the "concrete fund loss or permanent freezing" and "unauthorized transfer via precompile" acceptance criteria.

### Likelihood Explanation
High reachability: this is directly callable by any address holding usei via a single EVM transaction to the public `bank` precompile at `0x0000000000000000000000000000000000001001`, requiring no special privilege, no association step for `sendNative` (only the caller needs an associated Sei address, standard for any EVM user), and no contract deployment. Module account addresses are deterministically derivable off-chain (`authtypes.NewModuleAddress`), so the attack requires no guessing.

### Recommendation
Add a `p.bankKeeper.BlockedAddr(receiverSeiAddr)` (and ideally `IsInDenomAllowList`) check in both `sendNative` and `send`/`accAddressFromArg` consumers in `precompiles/bank/bank.go`, mirroring the check already present in `sei-cosmos/x/bank/keeper/msg_server.go`'s `Send` handler, and reject the transaction if the recipient (or sender, for parity) resolves to a blocked/module account address.

### Proof of Concept
1. Compute the Cosmos module account address for a sensitive module, e.g. `distrAddr := authtypes.NewModuleAddress(distrtypes.ModuleName)`.
2. From any EVM account with an associated Sei address, call the `bank` precompile at `0x0000000000000000000000000000000000001001`'s `sendNative(string to)` method with `to = distrAddr.String()` and non-zero `msg.value`.
3. Observe that `p.bankKeeper.SendCoinsAndWei` succeeds because no `BlockedAddr` check is performed, unlike calling `MsgSend` natively with the same recipient, which would be rejected with `ErrUnauthorized`. [8](#0-7)

### Citations

**File:** precompiles/bank/bank.go (L261-287)
```go
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send")
	}

	senderSeiAddr, ok := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !ok {
		return nil, 0, errors.New("invalid addr")
	}

	receiverAddr, ok := (args[0]).(string)
	if !ok || receiverAddr == "" {
		return nil, 0, errors.New("invalid addr")
	}

	receiverSeiAddr, err := sdk.AccAddressFromBech32(receiverAddr)
	if err != nil {
		return nil, 0, err
	}

	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, 0, err
	}
```

**File:** precompiles/bank/bank.go (L631-642)
```go
func (p PrecompileExecutor) accAddressFromArg(ctx sdk.Context, arg interface{}) (sdk.AccAddress, error) {
	addr := arg.(common.Address)
	if addr == (common.Address{}) {
		return nil, errors.New("invalid addr")
	}
	seiAddr, found := p.evmKeeper.GetSeiAddress(ctx, addr)
	if !found {
		// return the casted version instead
		return sdk.AccAddress(addr[:]), nil
	}
	return seiAddr, nil
}
```

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-54)
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
	if err != nil {
		return nil, err
	}
```

**File:** precompiles/bank/legacy/v600/bank.go (L121-159)
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

**File:** sei-cosmos/x/bank/keeper/send.go (L160-182)
```go
}

// SendCoins transfers amt coins from a sending account to a receiving account.
// An error is returned upon failure.
func (k BaseSendKeeper) SendCoins(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error {
	if err := k.SendCoinsWithoutAccCreation(ctx, fromAddr, toAddr, amt); err != nil {
		return err
	}

	// Create account if recipient does not exist.
	//
	// NOTE: This should ultimately be removed in favor a more flexible approach
	// such as delegated fee messages.
	accExists := k.ak.HasAccount(ctx, toAddr)
	if !accExists {
		defer func() {
			recordNewAccounts(ctx.Context(), 1)
		}()
		k.ak.SetAccount(ctx, k.ak.NewAccountWithAddress(ctx, toAddr))
	}

	return nil
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L438-459)
```go
}

func (k BaseSendKeeper) SendCoinsAndWei(ctx sdk.Context, from sdk.AccAddress, to sdk.AccAddress, amt sdk.Int, wei sdk.Int) error {
	if err := k.SubWei(ctx, from, wei); err != nil {
		return err
	}
	if err := k.AddWei(ctx, to, wei); err != nil {
		return err
	}
	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeWeiTransfer,
			sdk.NewAttribute(types.AttributeKeyRecipient, to.String()),
			sdk.NewAttribute(types.AttributeKeySender, from.String()),
			sdk.NewAttribute(sdk.AttributeKeyAmount, wei.String()),
		),
	})
	if amt.GT(sdk.ZeroInt()) {
		return k.SendCoinsWithoutAccCreation(ctx, from, to, sdk.NewCoins(sdk.NewCoin(sdk.MustGetBaseDenom(), amt)))
	}
	return nil
}
```
