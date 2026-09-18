## Bank Precompile `sendNative` Bypasses Bank-Module Send Restrictions (SendEnabled / DenomAllowList) Enforced by `MsgServer.Send` - (File: precompiles/bank/bank.go)

### Summary
This is the same bug class as the Morpho/Compound report: one execution path ("the underlying protocol") enforces a set of safety checks, while a second, functionally-equivalent path ("the wrapper") re-implements the state transition directly and omits those checks. In sei-chain, the standard Cosmos `bank.MsgServer.Send` enforces `IsSendEnabledCoins` (per-denom pause) and `IsInDenomAllowList`/`BlockedAddr` (denom allow-list/compliance restrictions) before moving funds. The EVM Bank precompile's `sendNative` method, reachable from any EVM transaction, calls `bankKeeper.SendCoinsAndWei` directly and never performs these checks.

### Finding Description
`sei-cosmos/x/bank/keeper/msg_server.go` `Send` explicitly checks pause and allow-list state before transferring funds: [1](#0-0) 

The Bank precompile at address `0x0000000000000000000000000000000000001001` exposes `sendNative`, which is reachable by any EVM transaction sending native value to the precompile. It resolves the sender/receiver Sei addresses and calls `bankKeeper.SendCoinsAndWei` directly, with no call to `IsSendEnabledCoins`, `IsInDenomAllowList`, or an explicit `BlockedAddr` check: [2](#0-1) 

The lower-level `BaseSendKeeper.SendCoins`/`AddCoins` path that `SendCoinsAndWei` ultimately uses only enforces `CanSendTo` (blocked recipient) via `AddCoins`, but the `IsSendEnabledCoins` (per-denom pause flag) and `IsInDenomAllowList` checks live exclusively in the `MsgServer.Send`/`MultiSend` handlers and in wasmd's `BankCoinTransferrer.TransferCoins`: [3](#0-2) [4](#0-3) 

Notably, the same precompile's `send` method (for non-native tokenfactory-style denoms) correctly routes through `bankMsgServer.Send`, which does perform these checks: [5](#0-4) 

This shows the checks were intentionally applied to one code path but not consistently applied to the sibling `sendNative` path, mirroring the Morpho/Compound situation where one action mimics the underlying protocol's checks but a sibling action does not.

### Impact Explanation
`SendEnabled`/`DefaultSendEnabled` is the mechanism operators use to pause fund movement for a given denom (e.g., during an active incident, security pause, or compliance freeze), and `DenomAllowList` restricts which addresses may hold/transfer a given (typically compliance-restricted) denom. Because `sendNative` bypasses both checks, any EVM caller can move `usei`/`wei` between Sei-associated accounts even while transfers of the base denom are administratively disabled or while an allow-list restriction is in force for that denom, defeating the intended freeze/compliance control. This is a fund-movement/authorization-bypass class issue rather than a direct fund-loss bug, but it can be used to circumvent an emergency pause used to prevent loss of funds during an active exploit, and to bypass allow-list-gated denom transfer policy.

### Likelihood Explanation
The precompile is part of the standard public EVM JSON-RPC surface — any address holding funds can call `sendNative` on `0x...1001` with no special privileges, so the path is trivially reachable by any unprivileged EVM transaction sender. The only precondition for real-world impact is that operators actually rely on `SendEnabled=false` or `DenomAllowList` for the affected denom, which is a supported and documented bank-module control.

### Recommendation
Apply the same `IsSendEnabledCoins` and `IsInDenomAllowList`/`BlockedAddr` checks in `sendNative` (and any other precompile method that calls `bankKeeper.SendCoins`/`SendCoinsAndWei` directly instead of going through `bankMsgServer.Send`) so that EVM-originated native transfers are subject to the same pause/allow-list guarantees as native Cosmos `MsgSend` transactions.

### Proof of Concept
1. Governance/operator sets `SendEnabled=false` for `usei` (or configures a `DenomAllowList` restricting transfers to specific addresses) via bank module params, intending to halt all fund movement.
2. An EVM account calls `sendNative(receiver)` on precompile address `0x0000000000000000000000000000000000001001` with non-zero `value`.
3. `precompiles/bank/bank.go`'s `sendNative` resolves sender/receiver Sei addresses and calls `p.bankKeeper.SendCoinsAndWei(...)` directly (`precompiles/bank/bank.go:285`), with no `IsSendEnabledCoins`/`IsInDenomAllowList` check performed anywhere in that code path.
4. The transfer succeeds despite the module-level pause/allow-list restriction, whereas an equivalent native `MsgSend` would have been rejected by `sei-cosmos/x/bank/keeper/msg_server.go:29-49`.

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

**File:** precompiles/bank/bank.go (L198-248)
```go
func (p PrecompileExecutor) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call send from staticcall")
	}
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, 0, err
	}
	denom := args[2].(string)
	if denom == "" {
		return nil, 0, errors.New("invalid denom")
	}
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, 0, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
	amount := args[3].(*big.Int)
	if amount.Cmp(utils.Big0) == 0 {
		// short circuit
		bz, err := method.Outputs.Pack(true)
		return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
	}
	senderSeiAddr, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		return nil, 0, err
	}
	receiverSeiAddr, err := p.accAddressFromArg(ctx, args[1])
	if err != nil {
		return nil, 0, err
	}

	msg := &banktypes.MsgSend{
		FromAddress: senderSeiAddr.String(),
		ToAddress:   receiverSeiAddr.String(),
		Amount:      sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount))),
	}

	err = msg.ValidateBasic()
	if err != nil {
		return nil, 0, err
	}

	if _, err = p.bankMsgServer.Send(sdk.WrapSDKContext(ctx), msg); err != nil {
		return nil, 0, err
	}

	bz, err := method.Outputs.Pack(true)
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
```

**File:** precompiles/bank/bank.go (L251-288)
```go
func (p PrecompileExecutor) sendNative(ctx sdk.Context, method *abi.Method, args []interface{}, caller common.Address, callingContract common.Address, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call sendNative from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall sendNative")
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
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
	accExists := p.accountKeeper.HasAccount(ctx, receiverSeiAddr)
```

**File:** sei-cosmos/x/bank/keeper/send.go (L162-182)
```go
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

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L1198-1220)
```go
// TransferCoins transfers coins from source to destination account when coin send was enabled for them and the recipient
// is not in the blocked address list.
func (c BankCoinTransferrer) TransferCoins(parentCtx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amount sdk.Coins) error {
	em := sdk.NewEventManager()
	ctx := parentCtx.WithEventManager(em)
	if err := c.keeper.IsSendEnabledCoins(ctx, amount...); err != nil {
		return err
	}
	if c.keeper.BlockedAddr(toAddr) {
		return sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", toAddr.String())
	}

	sdkerr := c.keeper.SendCoins(ctx, fromAddr, toAddr, amount)
	if sdkerr != nil {
		return sdkerr
	}
	for _, e := range em.Events() {
		if e.Type == sdk.EventTypeMessage { // skip messages as we talk to the keeper directly
			continue
		}
		parentCtx.EventManager().EmitEvent(e)
	}
	return nil
```
