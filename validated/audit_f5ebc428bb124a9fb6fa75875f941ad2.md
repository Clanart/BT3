Confirmed: the currently live bank precompile's `send` method routes through `p.bankMsgServer.Send(...)` [1](#0-0)  — which enforces `IsSendEnabledCoins`, `IsInDenomAllowList`, and `BlockedAddr` checks just like `MsgSend` [2](#0-1) . However, the `sendNative` method in the same active precompile calls `p.bankKeeper.SendCoinsAndWei(...)` directly, bypassing the msg-server-level `SendEnabled`/allow-list/`BlockedAddr` gates entirely.

### Title
Bank precompile `sendNative` bypasses SendEnabled/denom-allowlist/blocked-address checks enforced by `MsgSend` - (File: precompiles/bank/bank.go)

### Summary
The Cosmos `bank` module enforces token-transfer restrictions — `SendEnabled` (module/denom pause), per-denom `AllowList` (tokenfactory allow-listed denoms), and `BlockedAddr` (module/reserved account protection) — only inside the `msgServer.Send`/`MultiSend` handlers. The EVM `bank` precompile's `send` method correctly reuses this same gated path via `p.bankMsgServer.Send(...)`, but its sibling `sendNative` method calls `p.bankKeeper.SendCoinsAndWei(...)` directly on the keeper, which performs no `IsSendEnabledCoins`, `IsInDenomAllowList`, or `BlockedAddr` checks.

### Finding Description
`MsgSend` in the bank module explicitly checks `IsSendEnabledCoins`, `IsInDenomAllowList` for both sender and receiver, and `BlockedAddr` for the receiver before calling `SendCoins`: [3](#0-2) 

The `BaseSendKeeper.SendCoins`/`SendCoinsAndWei` keeper-level primitives themselves do not perform these authorization checks — `BlockedAddr` and `IsInDenomAllowList` are helper methods that callers must invoke explicitly: [4](#0-3) 

The EVM `bank` precompile's `send` (ERC20-pointer-only path) is careful to route through the message-server layer, inheriting all these checks: [1](#0-0) 

But `sendNative`, reachable by any EVM caller sending native `usei`/`wei` value to the precompile address, calls the raw keeper method directly: [5](#0-4) 

This is architecturally identical to the reported bug class: a protection (`SendEnabled`/allow-list/blocked-address gating) is enforced at one entry point (`MsgSend`) but is reachable and bypassable via a different entry point (`sendNative` on the EVM bank precompile) that calls the same underlying state-mutating primitive without re-checking the guard.

### Impact Explanation
If a tokenfactory denom (or the base `usei` denom) is ever put under `SendEnabled=false` (module pause) or has a denom `AllowList` configured restricting which addresses may send/receive it, or if `BlockedAddr` is used to protect a reserved/module account from receiving funds, any EVM user can bypass all of these protections by calling `sendNative` on the bank precompile (0x1001) instead of going through `MsgSend`. This allows unauthorized transfers to/from blocked or non-allow-listed addresses despite the on-chain safeguard, undermining the intended fund-movement restriction for the affected denom/account.

### Likelihood Explanation
High reachability: `sendNative` is a public, non-privileged EVM entry point on a well-known precompile address, callable by any transaction sender or contract with a simple value-bearing call. No special permissions, timing, or race conditions are required — the bypass is deterministic and always available whenever the restriction (SendEnabled=false, allow-list, or BlockedAddr) is configured for `usei`/wei transfers.

### Recommendation
Route `sendNative` through the same authorization checks used by `MsgSend`/`bankMsgServer.Send` (i.e., call `IsSendEnabledCoins`, `IsInDenomAllowList` for sender and receiver, and `BlockedAddr` for the receiver) before invoking `SendCoinsAndWei`, or refactor `SendCoinsAndWei` itself to perform these checks internally so all callers (module keepers, precompiles, msg servers) are consistently protected.

### Proof of Concept
1. Governance/admin sets `SendEnabled=false` for `usei` (or configures a `DenomAllowList` on a tokenfactory denom, or relies on `BlockedAddr` to protect a module account) intending to prevent transfers.
2. An EVM user calls the bank precompile at `0x0000000000000000000000000000000000001001` method `sendNative(receiverSeiAddr)` with `msg.value` set to the desired amount.
3. Execution flow: `PrecompileExecutor.sendNative` → `p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei)` — no `IsSendEnabledCoins`/`IsInDenomAllowList`/`BlockedAddr` check is performed.
4. The transfer succeeds despite the pause/allow-list/blocked-address restriction, whereas the same transfer attempted via a native `MsgSend` transaction would be rejected by `msgServer.Send`'s checks [6](#0-5) .

### Citations

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
