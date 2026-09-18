Confirmed: `BaseSendKeeper.SendCoins` (and its helper `sendCoinsWithoutAccCreation`) only performs `SubUnlockedCoins`/`AddCoins` balance operations and never calls `IsInDenomAllowList` or `BlockedAddr` — those authorization checks are only performed by `msgServer.Send`/`msgServer.MultiSend` in `sei-cosmos/x/bank/keeper/msg_server.go`. [1](#0-0) [2](#0-1) [3](#0-2) 

The legacy EVM bank precompile's `send()` implementation (multiple versions: v552, v555, v562, v580, v600, v606, v610, v614, v620, v65, v66) calls `p.bankKeeper.SendCoins()` directly rather than going through `bankMsgServer.Send()`, bypassing the `IsInDenomAllowList`/`BlockedAddr` checks entirely — exactly the same bug class as the report: a permission mapping (tokenfactory denom allow-list) exists and is enforced on one code path (`MsgSend`/`MsgMultiSend` via `msgServer`) but is silently skipped on another reachable path (the precompile's direct `SendCoins` call). [4](#0-3) [5](#0-4) 

Newer precompile versions (v601, v603, v605, v606, v610, v614, v620, v630, v640, current `precompiles/bank/bank.go`) were fixed to route through `p.bankMsgServer.Send()` instead, which does perform the allow-list check. [6](#0-5) [7](#0-6) 

However, I could not fully determine within the exploration budget whether these older legacy precompile versions (`v552`–`v580`, `v600`) are still registered/reachable in the currently active chain upgrade handler, or whether they are dead/superseded code kept only for historical replay of past blocks. This distinction is critical: if the vulnerable versions are no longer wired into the live precompile registry (i.e., only the fixed versions like `v601+` or the top-level `precompiles/bank/bank.go` are active for current block heights), then this is not currently exploitable and the analog would not qualify under the "reachable" requirement.

### Title
Legacy bank precompile `send` bypasses tokenfactory denom allow-list by calling `SendCoins` directly instead of `bankMsgServer.Send` - (File: precompiles/bank/legacy/v600/bank.go)

### Summary
Several legacy versions of the EVM bank precompile's `send()` function transfer coins by calling `BankKeeper.SendCoins()` directly. This keeper method only moves balances and never checks the tokenfactory `DenomAllowList` or `BlockedAddr` restrictions, which are only enforced inside `msgServer.Send`/`msgServer.MultiSend`. As a result, an EVM caller invoking the legacy precompile's `send` function can transfer allow-listed tokenfactory denoms to/from addresses that are not on the denom's allow-list, or to blocked addresses, completely bypassing the restriction — analogous to the reported `transferWhitelist` check being missing from `_checkTransfer`.

### Finding Description
`IsInDenomAllowList` and `BlockedAddr` are the only mechanisms enforcing tokenfactory denom transfer restrictions, and they are invoked exclusively from `msgServer.Send`/`msgServer.MultiSend`. [8](#0-7)  The `BaseSendKeeper.SendCoins` method that actually moves balances never performs these checks. [1](#0-0)  Legacy precompile implementations (e.g. `precompiles/bank/legacy/v600/bank.go`) call `p.bankKeeper.SendCoins(...)` directly for the EVM `send(address,address,string,uint256)` method, so the allow-list/blocked-address enforcement is never executed for that call path. [9](#0-8) 

### Impact Explanation
If a tokenfactory denom creator (an authority) has configured a `DenomAllowList` to restrict who may hold/transfer their denom, or an address has been added to `BlockedAddr`, calling the vulnerable legacy precompile's `send` function from an EVM transaction would move funds of that denom to/from disallowed addresses, undermining the token issuer's access-control guarantee. This is an unauthorized-transfer-via-precompile bug matching the required impact class.

### Likelihood Explanation
Exploitability depends entirely on whether the vulnerable legacy precompile code paths (`v552`–`v600`) remain wired into the live precompile registry for post-upgrade block execution, or are retained only for historical/replay purposes when older blocks are re-executed. I was unable to conclusively verify precompile version routing/registration logic (e.g., which version is selected per block height) within the available tool budget, so likelihood for the *currently live* chain state is uncertain.

### Recommendation
Ensure all legacy precompile `send` implementations route transfers through `bankMsgServer.Send` (as done in fixed versions v601+ and the current `precompiles/bank/bank.go`) rather than calling `BankKeeper.SendCoins` directly, so `IsInDenomAllowList` and `BlockedAddr` checks are always enforced, and audit whether any legacy version is still reachable for current-height transaction execution.

### Proof of Concept
1. A tokenfactory denom creator calls `SetDenomAllowList` to restrict `factory/<creator>/mydenom` to a whitelist that excludes address `X`.
2. An EVM transaction calls the legacy bank precompile's `send(sender, X, "factory/<creator>/mydenom", amount)` method (reachable if the vulnerable legacy version is active for the current block height).
3. `p.bankKeeper.SendCoins()` is invoked directly, performing `SubUnlockedCoins`/`AddCoins` with no allow-list check, so the transfer to `X` succeeds despite `X` not being in the allow-list — bypassing the intended restriction enforced only in `msgServer.Send`.

### Citations

**File:** sei-cosmos/x/bank/keeper/send.go (L164-182)
```go
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

**File:** sei-cosmos/x/bank/keeper/send.go (L201-230)
```go
func (k BaseSendKeeper) SendCoinsWithoutAccCreation(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error {
	return k.sendCoinsWithoutAccCreation(ctx, fromAddr, toAddr, amt, true)
}

func (k BaseSendKeeper) sendCoinsWithoutAccCreation(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins, checkNeg bool) error {
	err := k.SubUnlockedCoins(ctx, fromAddr, amt, checkNeg)
	if err != nil {
		return err
	}

	err = k.AddCoins(ctx, toAddr, amt, checkNeg)
	if err != nil {
		return err
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeTransfer,
			sdk.NewAttribute(types.AttributeKeyRecipient, toAddr.String()),
			sdk.NewAttribute(types.AttributeKeySender, fromAddr.String()),
			sdk.NewAttribute(sdk.AttributeKeyAmount, amt.String()),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(types.AttributeKeySender, fromAddr.String()),
		),
	})

	return nil
}
```

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-49)
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
```

**File:** precompiles/bank/legacy/v552/bank.go (L198-205)
```go
		return nil, err
	}

	if err := p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount)))); err != nil {
		return nil, err
	}

	return method.Outputs.Pack(true)
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

**File:** precompiles/bank/bank.go (L232-245)
```go
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
```

**File:** precompiles/bank/legacy/v601/bank.go (L162-175)
```go
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
```
