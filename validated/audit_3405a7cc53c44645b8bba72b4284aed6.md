## Title
Tokenfactory denom allow-list ACL bypass via the EVM bank precompile - (File: precompiles/bank/bank.go)

### Summary
The Cosmos `MsgSend`/`MsgMultiSend` handlers enforce a tokenfactory denom allow-list (`IsInDenomAllowList`) before calling `SendCoins`, but the EVM-reachable bank precompile calls the bank keeper's `SendCoins`/`SendCoinsAndWei` directly, without ever invoking that allow-list check — an alternate access path silently ignoring an ACL that is enforced on the "default" path, structurally analogous to CVE-2018-20145 (Mosquitto's `acl_file` being ignored on the default listener while enforced elsewhere).

### Finding Description
`x/bank`'s tokenfactory allow-list feature lets a denom admin restrict who may send/receive a `factory/...` denom via `AllowList`, enforced through `BaseSendKeeper.IsInDenomAllowList` [1](#0-0) . This check is only invoked from the Cosmos message server's `Send` and `MultiSend` handlers, immediately before calling `SendCoins`/`InputOutputCoins` [2](#0-1) [3](#0-2) .

A `grep` across the codebase shows `IsInDenomAllowList` is referenced only inside `sei-cosmos/x/bank/keeper/msg_server.go`, `sei-cosmos/x/bank/keeper/send.go`, and the `giga/deps/xbank` mirror/tests — it is never called from `precompiles/bank/bank.go`, even though that file calls `SendCoins`/`SendCoinsAndWei` directly on the bank keeper to move funds on behalf of an EVM caller. The precompile is a second, EVM-reachable "listener" onto the same underlying `SendCoins` primitive, but it does not go through the same ACL gate the Cosmos tx path enforces — exactly the class of bug described in the CVE (one entry point enforces the ACL, another entry point to the same resource does not, because the enforcement was added at the message-handler layer instead of inside the shared primitive it guards).

### Impact Explanation
If confirmed, any EVM account holding a tokenfactory denom with an admin-configured allow-list (e.g., a compliance/whitelist-gated asset) could transfer or receive that denom through the bank precompile even while excluded from the allow-list, defeating the admin's access control and enabling unauthorized transfer of restricted funds — a fund-loss/unauthorized-transfer-class impact reachable by any EVM transaction sender.

### Likelihood Explanation
Likelihood is high if the precompile path is confirmed unguarded: it requires only a standard EVM transaction calling the bank precompile's send function with a restricted tokenfactory denom, no privileged access needed, and no additional preconditions beyond the denom being both allow-listed and EVM-transferable.

### Recommendation
Move the `IsInDenomAllowList` check into `BaseSendKeeper.SendCoins` (and/or `SendCoinsAndWei`) itself so every caller — Cosmos `MsgSend`/`MsgMultiSend`, the EVM bank precompile, and any future callers — is uniformly gated, rather than re-implementing the check at each individual message/precompile handler.

### Proof of Concept
Not independently verified against the exact `precompiles/bank/bank.go` send implementation (index truncated the function bodies), so this should be validated by: (1) admin sets an `AllowList` on a `factory/{admin}/{denom}` excluding address X, (2) address X calls the bank precompile's send/transfer method on that denom from an EVM tx, (3) confirm whether the transfer succeeds despite X not being in the allow-list — if it succeeds, the bypass is confirmed.

**Note on completeness:** I could not read the full body of `precompiles/bank/bank.go`'s send/transfer functions due to index size limits, so the exact call site and whether any equivalent guard exists elsewhere (e.g., an ante-handler-level check) is unconfirmed. Starting a Devin session with full repository access would let a background agent read `precompiles/bank/bank.go` in full and confirm definitively whether `IsInDenomAllowList` is truly absent from that call path before treating this as a validated finding rather than a plausible analog.

### Citations

**File:** sei-cosmos/x/bank/keeper/send.go (L506-524)
```go
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

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L74-99)
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

	err := k.InputOutputCoins(ctx, msg.Inputs, msg.Outputs)
	if err != nil {
		return nil, err
	}
```
