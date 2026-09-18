### Title
Tokenfactory denom allow-list restriction is bypassed for CosmWasm contract funds transfers - (File: sei-wasmd/x/wasm/keeper/keeper.go)

### Summary
The tokenfactory "denom allow list" restriction (used to permission which addresses may hold/transfer a given `factory/...` denom) is enforced only inside the bank module's `MsgServer.Send` / `MsgServer.MultiSend` handlers via `IsInDenomAllowList`, and is never enforced inside the lower-level `BaseSendKeeper.SendCoins` / `SendCoinsWithoutAccCreation` / `InputOutputCoins` primitives that those handlers call. Any other code path that reaches these primitives directly moves coins without ever checking the allow list — exactly the "modifier applied to the wrong function" pattern from the external report (`onlyUnblockedTokens` on `withdrawERC20()` but not on the equivalent `depositAndLock`/`withdrawLPToken` path).

### Finding Description
`IsInDenomAllowList` is called only from: [1](#0-0) [2](#0-1) 

The underlying transfer primitives it is supposed to protect do not perform this check themselves: [3](#0-2) [4](#0-3) 

`sei-wasmd`'s CosmWasm keeper uses a dedicated `BankCoinTransferrer.TransferCoins` helper to move the `Funds` attached to `MsgInstantiateContract`/`MsgExecuteContract` from the sender to the target contract. This helper only checks `IsSendEnabledCoins` and `BlockedAddr`, and calls `keeper.SendCoins` directly — it never calls `IsInDenomAllowList`: [5](#0-4) 

Because a CosmWasm user attaches `Funds` to an `Execute`/`Instantiate` message rather than issuing a `MsgSend`/`MsgMultiSend`, the transfer never goes through `msgServer.Send`/`msgServer.MultiSend`, so the tokenfactory allow-list restriction set via `SetDenomAllowList` (exposed through `MsgUpdateDenom`) is silently bypassed for both the sender and the recipient (the contract). [6](#0-5) [7](#0-6) 

This mirrors the report's root cause: a security check ("onlyUnblockedTokens"/allow-list) is only wired into one entry point (`withdrawERC20`/`MsgSend`), while a functionally-equivalent alternate entry point (`depositAndLock`+`withdrawLPToken`/CW `Funds` transfer via `TransferCoins`) achieves the same state change without the guard.

### Impact Explanation
A tokenfactory denom admin can restrict a denom to a specific address allow list (e.g., for compliance/KYC or controlled-distribution tokens) via `MsgUpdateDenom`. This restriction is a security boundary enforced at consensus level for ordinary transfers. By simply attaching the restricted denom as `Funds` to any `MsgExecuteContract`/`MsgInstantiateContract` call, an unprivileged sender who is *not* in the allow list can move that denom to a contract, and the contract (also not in the allow list) can receive/hold it — completely defeating the allow-list access control. This is an unauthorized transfer of a permissioned asset, which is a fund-safety / access-control impact (Medium/High depending on how the allow list is relied upon by dependent contracts/integrations).

### Likelihood Explanation
High — any CosmWasm user can trigger this with a single `MsgExecuteContract`/`MsgInstantiateContract` transaction carrying `Funds` in the restricted denom; no privileged role or special setup is required beyond the denom already having an allow list configured (a normal, documented tokenfactory feature).

### Recommendation
Enforce `IsInDenomAllowList` (and `BlockedAddr`) inside the shared `BaseSendKeeper.SendCoins`/`SendCoinsWithoutAccCreation`/`InputOutputCoins` primitives themselves (or at minimum inside `BankCoinTransferrer.TransferCoins` in `sei-wasmd/x/wasm/keeper/keeper.go`), so the restriction is applied consistently regardless of the calling path, instead of being duplicated only in the bank `MsgServer.Send`/`MultiSend` handlers.

### Proof of Concept
1. Tokenfactory admin creates `factory/admin/restricted` and calls `MsgUpdateDenom` with `AllowList = {admin}` (`x/tokenfactory/keeper/msg_server.go:57-92`), restricting the denom to itself only.
2. Admin uploads/instantiates a simple CW contract (any contract accepting funds, e.g. via `Instantiate`).
3. Attacker (address not on the allow list) is given some `restricted` tokens by the admin directly minting to them via tokenfactory mint authority, or by any other in-allow-list transfer initially. (Not required if attacker is the module/admin itself acting maliciously beyond intended scope — but even a single legitimately-funded holder can now move funds outside the allow list.)
4. Attacker submits `MsgExecuteContract{Sender: attacker, Contract: contractAddr, Funds: sdk.Coins{restricted-denom}}`.
5. `sei-wasmd` processes the funds transfer through `BankCoinTransferrer.TransferCoins` → `BaseSendKeeper.SendCoins`, which only checks `IsSendEnabledCoins`/`BlockedAddr`, never `IsInDenomAllowList`; the transfer succeeds even though neither `attacker` nor `contractAddr` is in the denom's allow list — bypassing the restriction that an equivalent `MsgSend` of the same denom to the same recipient would have rejected via `sei-cosmos/x/bank/keeper/msg_server.go:43-49`.

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

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L74-94)
```go
func (k msgServer) MultiSend(goCtx context.Context, msg *types.MsgMultiSend) (*types.MsgMultiSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	denomToAllowListCache := make(map[string]AllowedAddresses)
	// NOTE: totalIn == totalOut should already have been checked
	for _, in := range msg.Inputs {
		if err := k.IsSendEnabledCoins(ctx, in.Coins...); err != nil {
			return nil, err
		}
		accAddr := sdk.MustAccAddressFromBech32(in.Address)
		if !k.IsInDenomAllowList(ctx, accAddr, in.Coins, denomToAllowListCache) {
			return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", accAddr)
		}
	}

	for _, out := range msg.Outputs {
		accAddr := sdk.MustAccAddressFromBech32(out.Address)

		if k.BlockedAddr(accAddr) || !k.IsInDenomAllowList(ctx, accAddr, out.Coins, denomToAllowListCache) {
			return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", out.Address)
		}
	}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L100-160)
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

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L1186-1221)
```go
// BankCoinTransferrer replicates the cosmos-sdk behaviour as in
// https://github.com/cosmos/cosmos-sdk/blob/v0.41.4/x/bank/keeper/msg_server.go#L26
type BankCoinTransferrer struct {
	keeper types.BankKeeper
}

func NewBankCoinTransferrer(keeper types.BankKeeper) BankCoinTransferrer {
	return BankCoinTransferrer{
		keeper: keeper,
	}
}

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
}
```

**File:** x/tokenfactory/keeper/msg_server.go (L57-92)
```go
func (server msgServer) UpdateDenom(goCtx context.Context, msg *types.MsgUpdateDenom) (*types.MsgUpdateDenomResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	denom, err := server.validateUpdateDenom(ctx, msg)
	if err != nil {
		return nil, err
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, denom)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	updateDenomEvent := sdk.NewEvent(
		types.TypeMsgUpdateDenom,
		sdk.NewAttribute(types.AttributeCreator, msg.Sender),
		sdk.NewAttribute(types.AttributeUpdatedTokenDenom, denom),
	)

	if msg.AllowList != nil {
		server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)
		updateDenomEvent = updateDenomEvent.AppendAttributes(
			sdk.NewAttribute(types.AttributeAllowList, strings.Join(msg.AllowList.Addresses, ",")),
		)
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		updateDenomEvent,
	})

	return &types.MsgUpdateDenomResponse{}, nil
}
```
