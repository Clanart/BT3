### Title
CosmWasm contract calls, IBC transfers, and tokenfactory keeper transfers bypass the token-factory `IsInDenomAllowList` restriction that `MsgSend` enforces - (File: sei-cosmos/x/bank/keeper/send.go)

### Summary
The bank module's `x/bank/keeper/msg_server.go` `Send` handler enforces the token-factory denom allow-list restriction via `IsInDenomAllowList` for both the sender and recipient before calling `SendCoins`. [1](#0-0)  However, the underlying `BaseSendKeeper.SendCoins` / `SendCoinsWithoutAccCreation` / `InputOutputCoins` functions that actually move balances do not perform this check themselves. [2](#0-1)  Any code path that calls these lower-level keeper methods directly — instead of going through `MsgServer.Send` — silently bypasses the allow-list restriction, exactly analogous to the JOJO bug where the internal deposit path skipped a check enforced only in the external entrypoint.

### Finding Description
`IsInDenomAllowList` is defined once in `BaseSendKeeper` and is only invoked from `msgServer.Send` in `sei-cosmos/x/bank/keeper/msg_server.go`. [3](#0-2)  `SendCoins`, `SendCoinsWithoutAccCreation`, and `InputOutputCoins` — the methods used by every other subsystem to move coins — do not call `IsInDenomAllowList` at all. [4](#0-3) 

This matters because token-factory denom allow-lists are a security primitive: a denom creator can restrict which addresses may hold/transfer a factory denom via `SetDenomAllowList`, and `MsgSend` is the only message-level entrypoint that enforces it. [5](#0-4)  Multiple reachable paths call `SendCoins`/`TransferCoins` directly, bypassing this restriction:
- CosmWasm contract `funds` transfers go through `wasmd`'s `BankCoinTransferrer.TransferCoins`, which only checks `IsSendEnabledCoins` and `BlockedAddr`, never `IsInDenomAllowList`, before calling `SendCoins`. [6](#0-5) 
- The `bank` EVM precompile's `sendNative` method calls `bankKeeper.SendCoinsAndWei` directly, again without any allow-list check. [7](#0-6)  (Note: `sendNative` only moves `usei`/wei, not factory denoms, so this specific call is not directly exploitable for factory-denom allow-lists, but it illustrates the same missing-check pattern at the keeper layer.)
- The `bank` precompile's `send` method (used by ERC20 pointer contracts for factory/native denoms) does route through `bankMsgServer.Send`, so it is protected. [8](#0-7) 

The most concrete bypass is via CosmWasm: any `MsgExecuteContract`/`MsgInstantiateContract` that attaches `funds` in a restricted factory denom moves coins through `BankCoinTransferrer.TransferCoins` → `SendCoins`, completely skipping the allow-list gate that `MsgSend` would have enforced for the same transfer.

### Impact Explanation
This allows a user to circumvent a denom creator's allow-list access control on token-factory denoms by routing a transfer through a CosmWasm contract call instead of a plain `MsgSend`. Any protocol or issuer relying on `SetDenomAllowList` to gate holders/participants of a restricted asset (e.g., permissioned token or KYC'd asset) can have that restriction bypassed entirely, moving restricted funds to/from unauthorized addresses. This is an "unauthorized transfer" bypassing an authorization mechanism reachable from an ordinary wasm contract-execute transaction.

### Likelihood Explanation
High likelihood of reachability: attaching `funds` to a `MsgExecuteContract`/`MsgInstantiateContract` call against any deployed CosmWasm contract (even one that does nothing with the funds, or that forwards them) is a completely standard, unprivileged user action requiring no special permission. The only precondition is that a token-factory denom with a `DenomAllowList` exists — which is itself a normal, single-transaction admin action available to any token-factory denom creator.

### Recommendation
Move the `IsInDenomAllowList` (and ideally `BlockedAddr` for recipients) enforcement down into the shared keeper methods (`SendCoins`, `SendCoinsWithoutAccCreation`, `InputOutputCoins`) rather than only in `MsgServer.Send`, so every caller — wasmd's `BankCoinTransferrer`, IBC transfer, and any future integration — is subject to the same allow-list restriction. Alternatively, audit and explicitly add the allow-list check to `sei-wasmd/x/wasm/keeper/keeper.go`'s `BankCoinTransferrer.TransferCoins` and any other direct `SendCoins` callers that handle user-supplied denoms.

### Proof of Concept
1. As the creator of a token-factory denom `factory/<creator>/restricted`, call `SetDenomAllowList` (via `MsgCreateDenom` with an `AllowList`, or a subsequent update) restricting the denom to a specific set of addresses. [9](#0-8) 
2. Fund an address `A` that is NOT in the allow-list with some of the restricted denom (e.g., minted before the allow-list was set, or transferred to it before restriction).
3. Deploy or pick any CosmWasm contract that accepts `funds` on `execute`/`instantiate` (even a no-op contract).
4. Address `A` submits `MsgExecuteContract` with `funds` = the restricted denom, sent to address `B` (also not on the allow-list, or the reverse — attempt a transfer that `MsgSend` would reject).
5. Observe that the transfer succeeds through `wasmd`'s `BankCoinTransferrer.TransferCoins` → `SendCoins`, since only `IsSendEnabledCoins` and `BlockedAddr` are checked there — `IsInDenomAllowList` is never invoked — whereas the equivalent `MsgSend` between the same two addresses would be rejected with `"is not allowed to send/receive funds"`. [6](#0-5) [10](#0-9)

### Citations

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

**File:** sei-cosmos/x/bank/keeper/send.go (L100-182)
```go
// InputOutputCoins performs multi-send functionality. It accepts a series of
// inputs that correspond to a series of outputs. It returns an error if the
// inputs and outputs don't lineup or if any single transfer of tokens fails.
func (k BaseSendKeeper) InputOutputCoins(ctx sdk.Context, inputs []types.Input, outputs []types.Output) error {
	// Safety check ensuring that when sending coins the keeper must maintain the
	// Check supply invariant and validity of Coins.
	if err := types.ValidateInputsOutputs(inputs, outputs); err != nil {
		return err
	}
	for _, in := range inputs {
		inAddress, err := sdk.AccAddressFromBech32(in.Address)
		if err != nil {
			return err
		}

		err = k.SubUnlockedCoins(ctx, inAddress, in.Coins, true)
		if err != nil {
			return err
		}

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				sdk.EventTypeMessage,
				sdk.NewAttribute(types.AttributeKeySender, in.Address),
			),
		)
	}

	for _, out := range outputs {
		outAddress, err := sdk.AccAddressFromBech32(out.Address)
		if err != nil {
			return err
		}
		err = k.AddCoins(ctx, outAddress, out.Coins, true)
		if err != nil {
			return err
		}

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeTransfer,
				sdk.NewAttribute(types.AttributeKeyRecipient, out.Address),
				sdk.NewAttribute(sdk.AttributeKeyAmount, out.Coins.String()),
			),
		)

		// Create account if recipient does not exist.
		//
		// NOTE: This should ultimately be removed in favor a more flexible approach
		// such as delegated fee messages.
		accExists := k.ak.HasAccount(ctx, outAddress)
		if !accExists {
			defer func() {
				recordNewAccounts(ctx.Context(), 1)
			}()
			k.ak.SetAccount(ctx, k.ak.NewAccountWithAddress(ctx, outAddress))
		}
	}

	return nil
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

**File:** sei-cosmos/x/bank/keeper/send.go (L501-524)
```go
// IsInDenomAllowList checks if the given address is allowed to send the given coins.
// The check is performed only fot token factory denoms. For each token factory denom,
// it checks if there is allow list for the given denom. If there is no allow list,
// the address is allowed to send the coins. If there is an allow list, the address is
// allowed to send the coins only if it is in the allow list.
func (k BaseSendKeeper) IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool {
	for _, coin := range coins {
		// Skip if denom does not contain the token factory prefix
		if !strings.HasPrefix(coin.Denom, TokenFactoryPrefix) {
			continue
		}

		allowedAddresses := k.getAllowedAddresses(ctx, cache, coin.Denom)
		// skip if there is no allow list for the denom
		if len(allowedAddresses.set) == 0 {
			continue
		}

		if !allowedAddresses.contains(addr) {
			return false
		}
	}
	return true
}
```

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L1198-1213)
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

**File:** precompiles/bank/bank.go (L280-287)
```go
	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, 0, err
	}
```

**File:** x/tokenfactory/keeper/createdenom_test.go (L88-95)
```go
		{
			desc:     "valid allow list",
			subdenom: "withallowlist",
			allowList: &banktypes.AllowList{
				Addresses: []string{suite.TestAccs[0].String(), suite.TestAccs[1].String(), suite.TestAccs[2].String()},
			},
			valid: true,
		},
```
