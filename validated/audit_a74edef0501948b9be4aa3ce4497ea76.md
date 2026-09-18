## Title
CosmWasm contracts can move tokenfactory-restricted coins by bypassing the bank `DenomAllowList` circuit breaker - (File: sei-wasmd/x/wasm/keeper/keeper.go)

## Summary
Sei's tokenfactory `DenomAllowList` is a per-denom transfer restriction (an authority-controlled circuit breaker analogous to Blend's pool status), enforced by `IsInDenomAllowList` checks. Those checks are only applied in the `x/bank` message-server entry points (`MsgSend`/`MsgMultiSend`), not inside the lower-level `SendCoins`/`InputOutputCoins` keeper functions themselves. `sei-wasmd`'s `BankCoinTransferrer.TransferCoins`, which is the bank-transfer path used for `BankMsg::Send` executed from inside CosmWasm contracts, calls `SendCoins` directly and only checks `IsSendEnabledCoins` and `BlockedAddr` — it never calls `IsInDenomAllowList`. This mirrors the Blend report exactly: a circuit-breaker check that lives in one call-path (`MsgSend`) is missing from an alternate call-path (`BankMsg` dispatched from a wasm contract) that reaches the same underlying state-mutating primitive.

## Finding Description
`IsInDenomAllowList` restricts sending/receiving a tokenfactory denom to an allow-listed set of addresses, set via `SetDenomAllowList`. [1](#0-0) 

This restriction is enforced in the standard `x/bank` `MsgSend` and `MsgMultiSend` handlers: [2](#0-1) [3](#0-2) 

However, the underlying keeper primitive `SendCoins`/`InputOutputCoins` does not itself perform this check — the allow-list gate lives only in `msgServer.Send`/`MultiSend`, not in the keeper method that actually moves balances. Any other caller of `SendCoins` that doesn't replicate the `IsInDenomAllowList` gate will silently bypass the restriction, exactly like Blend's `execute_flash_loan` calling the borrow primitive without replicating `require_action_allowed`.

`sei-wasmd`'s `BankCoinTransferrer.TransferCoins` — the transfer path wired up for CosmWasm contract-initiated `BankMsg::Send`/`BankMsg::Burn` execution (`NewBankCoinTransferrer`) — is one such caller. It only checks `IsSendEnabledCoins` and `BlockedAddr` before calling `SendCoins` directly: [4](#0-3) 

There is no call to `IsInDenomAllowList` for either the source or destination address in this path, whereas the equivalent `MsgSend` path enforces it for both sender and receiver.

## Impact Explanation
An authority that has restricted a tokenfactory denom to a specific allow-list (e.g. for compliance, sanctions-list enforcement, or a controlled distribution) intends that restriction to be a hard boundary on all transfers of that denom. Any CosmWasm contract — deployable and callable by any unprivileged user — that receives such a restricted denom and forwards it via `BankMsg::Send` can move the coins to or from addresses that are not on the allow list, defeating the authority's transfer restriction entirely. This is an unauthorized-transfer / bypassed-authority-control class of finding on funds that are supposed to be movement-restricted.

## Likelihood Explanation
Reaching this path requires only: (1) a tokenfactory denom with an active `DenomAllowList`, and (2) any wasm contract capable of executing `BankMsg::Send` with that denom (trivial — any CosmWasm contract can do this, and no special privileges are needed to deploy/instantiate/execute a contract). No governance, validator, or admin privileges are needed by the attacker; only the denom authority needs to have set up an allow list, which is a normal, permissionless-to-encounter configuration for tokenfactory denoms designed to restrict transfers.

## Recommendation
Add the same `IsInDenomAllowList` checks (for both sender and recipient) to `BankCoinTransferrer.TransferCoins` in `sei-wasmd/x/wasm/keeper/keeper.go`, mirroring the checks already present in `sei-cosmos/x/bank/keeper/msg_server.go`'s `Send`/`MultiSend` handlers. More robustly, move the `IsInDenomAllowList` (and ideally `BlockedAddr`) enforcement into the shared `SendCoins`/`InputOutputCoins` keeper primitives themselves so that every caller — message server, wasm bank plugin, precompiles, or future callers — inherits the restriction instead of having to replicate it per call-site.

## Proof of Concept
1. Denom authority creates a tokenfactory denom `factory/<authority>/restricted` and calls `SetDenomAllowList` to restrict transfers to `{authority, allowedUser}`.
2. Authority funds a CosmWasm contract `C` (or an account that then funds `C`) with `restricted` coins — funding via `MintCoins`/module transfer is unaffected since it isn't a `MsgSend`.
3. Any user calls `C`'s execute entrypoint, which internally issues a `BankMsg::Send{ to_address: attacker, amount: restricted }`.
4. The wasm bank plugin routes this through `BankCoinTransferrer.TransferCoins`, which checks only `IsSendEnabledCoins` and `BlockedAddr(attacker)` and then calls `SendCoins` directly — `attacker` not being in the allow list is never checked, and the transfer succeeds.
5. Compare with step 4 done via a direct `MsgSend{from: C, to: attacker, amount: restricted}` — this would be rejected with `"<attacker> is not allowed to receive funds"` by `msgServer.Send`'s `IsInDenomAllowList` check.

Note: I was not able to fully trace how `NewBankCoinTransferrer` is registered into the wasm keeper's `bankPlugin`/`Messenger` wiring within the indexed portion of the codebase (only its definition and one other reference in `wasmtesting/coin_transferrer.go` were found), so the exact wiring point (and whether any alternate wrapper elsewhere re-adds the allow-list check before reaching `TransferCoins`) could not be fully confirmed from the available index. A Devin session with full repository access should confirm the `Messenger`/`BankCoinTransferrer` wiring in `sei-wasmd/x/wasm/keeper` before treating this as a fully confirmed production-reachable path.

### Citations

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

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L1198-1221)
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
}
```
