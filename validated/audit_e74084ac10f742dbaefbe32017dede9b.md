### Title
CosmWasm `BankMsg::Send` bypasses tokenfactory denom allow-list checks, breaking the guarantee enforced by `MsgSend`/`MsgMultiSend` - ([File: sei-wasmd/x/wasm/keeper/keeper.go])

### Summary
The tokenfactory denom allow-list check (`IsInDenomAllowList`) is only enforced at the `x/bank` message-server layer (`MsgSend`/`MsgMultiSend`), not inside the underlying `BaseSendKeeper.SendCoins`/`InputOutputCoins` primitives. Any other code path that calls `SendCoins` directly — most notably the CosmWasm `BankCoinTransferrer.TransferCoins` used to execute a contract's `BankMsg::Send` — never calls `IsInDenomAllowList`, so a restricted (non-allow-listed) sender or receiver of a tokenfactory allow-listed denom can move funds simply by routing them through a CosmWasm contract instead of a plain `MsgSend`.

### Finding Description
`x/bank`'s `msgServer.Send` and `msgServer.MultiSend` explicitly gate both sender and receiver against `IsInDenomAllowList` before calling `SendCoins`/`InputOutputCoins`: [1](#0-0) 

However, `IsInDenomAllowList` is defined on `BaseSendKeeper` but is **not invoked from within** `SendCoins` or `InputOutputCoins` themselves — it's purely an opt-in check that each caller must perform: [2](#0-1) 

CosmWasm's `BankCoinTransferrer.TransferCoins` (used by `x/wasm` to execute a contract-issued `BankMsg::Send`) only checks `IsSendEnabledCoins` and `BlockedAddr(toAddr)` before calling `SendCoins` directly — it never calls `IsInDenomAllowList` for either the sender or the receiver: [3](#0-2) 

This is structurally identical to the KUMA `approve()` bug: one entry point (`msgServer.Send`, analogous to `transferFrom`) performs the authorization/blacklist check, while a second, equally-reachable entry point (`BankCoinTransferrer.TransferCoins`, analogous to `approve`) that operates on the *same underlying state-mutating primitive* (`SendCoins`) omits it.

### Impact Explanation
Any user can deploy or invoke a CosmWasm contract that issues a `BankMsg::Send` on their behalf. If the sender or receiver holds a tokenfactory denom that an admin has restricted via `SetDenomAllowList` (e.g., a permissioned/whitelisted RWA or compliance-gated token), that restriction can be trivially bypassed by wrapping the transfer inside a CW contract call instead of submitting a raw `MsgSend`. This defeats the entire purpose of the denom allow-list feature and enables unauthorized transfer of a restricted asset — a direct violation of the token issuer's access-control guarantee, satisfying "unauthorized transfer" / fund-movement-restriction bypass impact.

### Likelihood Explanation
High likelihood: any unprivileged user who can submit a `MsgExecuteContract` (or instantiate a trivial pass-through contract) can trigger `BankMsg::Send`, which is standard, well-documented CosmWasm functionality reachable on any Sei network that has `x/wasm` enabled (which is the default). No special privileges, precompile access, or validator cooperation are required — only a restricted tokenfactory denom with an active allow list.

### Recommendation
Add the same `IsInDenomAllowList` check (for both `fromAddr` and `toAddr`) inside `BankCoinTransferrer.TransferCoins` in `sei-wasmd/x/wasm/keeper/keeper.go`, mirroring the checks already performed in `sei-cosmos/x/bank/keeper/msg_server.go`'s `Send`/`MultiSend`. More robustly, move the allow-list enforcement into `BaseSendKeeper.SendCoins`/`InputOutputCoins` themselves so that every caller (bank message server, wasmd, and any future integration) is guaranteed to respect the denom allow list rather than relying on each caller to opt in.

### Proof of Concept
1. Chain admin creates a tokenfactory denom `factory/{admin}/restricted` and calls `SetDenomAllowList` with only `{admin}` in the allow list.
2. Admin funds account `A` with `100 restricted` and account `A` is NOT in the allow list (or the intended flow is that only allow-listed accounts may hold/move the token).
3. If `A` submits `MsgSend` to `B`, `msgServer.Send` calls `IsInDenomAllowList(ctx, A, ...)` and rejects with `"A is not allowed to send funds"` per [4](#0-3) .
4. Instead, `A` instantiates/invokes a simple CosmWasm contract that issues `BankMsg::Send{ to_address: B, amount: [100 restricted] }` on `A`'s behalf (contract acts as the caller-forwarding wallet, or `A` funds the contract and it forwards). This routes through `x/wasm`'s `BankCoinTransferrer.TransferCoins`, which only checks `IsSendEnabledCoins` and `BlockedAddr(B)` before calling `SendCoins` — bypassing the allow-list check entirely and successfully transferring the restricted tokens.

### Citations

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
