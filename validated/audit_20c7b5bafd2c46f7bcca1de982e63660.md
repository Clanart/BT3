## Analog Found

### Title
Native usei/wei EVM transfers bypass `IsSendEnabledCoins`/`IsInDenomAllowList` gating enforced only by the bank `MsgSend` path - ([File: x/evm/state/balance.go])

### Summary
The bank module gates fund movement behind three checks — `IsSendEnabledCoins` (per-denom send toggle), `IsInDenomAllowList` (tokenfactory allow-list), and `BlockedAddr` — but these checks are only enforced in the `MsgSend`/`MsgMultiSend` msg-server entrypoints, not in the underlying `SendCoins`/`SendCoinsAndWei` primitives. Any code path that calls the low-level keeper methods directly instead of going through the msg server silently skips these gates, exactly mirroring the microweber advisory's pattern: a business rule enforced only at one API surface (the "coupon" endpoint / `MsgSend`) and bypassable via a different reachable path that hits the same underlying effect directly.

### Finding Description
`bank`'s `msgServer.Send` is the only place that calls `IsSendEnabledCoins` and `IsInDenomAllowList` before invoking `SendCoins`: [1](#0-0) 

The underlying `SendCoins` / `SendCoinsAndWei` primitives implement none of these checks themselves — only `BlockedAddr`-adjacent `CanSendTo` is checked inside `AddCoins`/`AddWei`, and even `BlockedAddr` proper is enforced only in `SendCoinsFromModuleToAccount`, not in `SendCoins`/`SendCoinsAndWei`: [2](#0-1) [3](#0-2) 

Every native-token movement caused by a plain EVM value transfer (a standard ETH-style `to`/`value` transaction, reachable by any EVM sender) flows through the EVM StateDB's `SubBalance`/`AddBalance`, which call `SubUnlockedCoins`/`AddCoins`/`SubWei`/`AddWei` directly — never `IsSendEnabledCoins` or `IsInDenomAllowList`: [4](#0-3) [5](#0-4) 

The `DBImpl.send` helper (used by `SetBalance` during simulation and by the bank precompile's `sendNative`/`HandlePaymentUseiWei` paths) also calls `SendCoinsAndWei` directly, bypassing the same gates: [6](#0-5) [7](#0-6) 

Governance-controlled send-enable toggling (`bank` `SendEnabled`/`DefaultSendEnabled` params, exposed via the `bank` precompile's `params()` query) and tokenfactory denom allow-lists (`SetDenomAllowList`) are meant to be authoritative gates on token movement, but any transfer that reaches the EVM balance-mutation path for `usei`/wei — an ordinary value-transfer EVM transaction, or the bank precompile's `sendNative`/pointer-payment flows — never consults them.

### Impact Explanation
If governance or an admin disables sending of the chain's base denom (`usei`) via `bank` `SendEnabled` params (e.g., during an incident or a compliance freeze), or restricts a tokenfactory denom's transferability via `SetDenomAllowList`, any user can still move `usei`/wei between arbitrary Sei-mapped addresses by simply sending a native-value EVM transaction, because the EVM balance path never calls `IsSendEnabledCoins`/`IsInDenomAllowList`. This is a direct, unauthorized-transfer/fund-movement-restriction bypass reachable from a single unprivileged EVM transaction, matching the "obtain a lower-privilege outcome despite the admin disabling the control" pattern of CVE-2023-6832.

### Likelihood Explanation
Any account with an EVM/Sei address association and a nonzero `usei`/wei balance can trigger this by sending a plain value-transfer EVM transaction — no special permissions, precompile access, or contract deployment required, and no ante-handler or EVM-execution check re-validates `IsSendEnabledCoins`/`IsInDenomAllowList` before `AddBalance`/`SubBalance` run.

### Recommendation
Enforce `IsSendEnabledCoins` and `IsInDenomAllowList` (in addition to the existing `CanSendTo`/`BlockedAddr` checks) inside the shared low-level primitives (`SendCoins`, `SendCoinsAndWei`, or at minimum in the EVM `DBImpl.AddBalance`/`SubBalance` path) so that all fund-movement entrypoints — Cosmos `MsgSend` and EVM native transfers/precompile-driven transfers alike — respect the same governance-controlled send-enable and allow-list gates.

### Proof of Concept
1. Governance/admin sets `bank` params so `usei` (or a tokenfactory denom) has `SendEnabled=false`, or configures a `DenomAllowList` restricting who may send/receive a tokenfactory denom.
2. An attacker with an EVM-associated Sei address holding `usei`/the restricted denom submits a standard EVM transaction with `to` = victim/recipient address and `value` > 0 (a plain native transfer, no contract call needed).
3. `x/evm` applies the transfer via `StateDB.SubBalance`/`AddBalance` → `BankKeeper().SubUnlockedCoins`/`AddCoins`/`SubWei`/`AddWei`, none of which call `IsSendEnabledCoins` or `IsInDenomAllowList`.
4. The transfer succeeds and funds move despite the disabled/restricted state, exactly as `MsgSend` would have rejected it. This cannot be fully validated end-to-end without executing a live chain/test (index-search only), but the code paths cited above show no gate present on the EVM balance-mutation route.

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

**File:** sei-cosmos/x/bank/keeper/send.go (L440-459)
```go
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

**File:** x/evm/state/balance.go (L36-50)
```go
	// Hook for mock balances (no-op in production builds)
	s.ensureSufficientBalance(evmAddr, amt)

	usei, wei := SplitUseiWeiAmount(amt)
	addr := s.getSeiAddress(evmAddr)
	err := s.k.BankKeeper().SubUnlockedCoins(ctx, addr, sdk.NewCoins(sdk.NewCoin(s.k.GetBaseDenom(s.ctx), usei)), true)
	if err != nil {
		s.err = err
		return *ZeroInt
	}
	err = s.k.BankKeeper().SubWei(ctx, addr, wei)
	if err != nil {
		s.err = err
		return *ZeroInt
	}
```

**File:** x/evm/state/balance.go (L84-95)
```go
	usei, wei := SplitUseiWeiAmount(amt)
	addr := s.getSeiAddress(evmAddr)
	err := s.k.BankKeeper().AddCoins(ctx, addr, sdk.NewCoins(sdk.NewCoin(s.k.GetBaseDenom(s.ctx), usei)), true)
	if err != nil {
		s.err = err
		return *ZeroInt
	}
	err = s.k.BankKeeper().AddWei(ctx, addr, wei)
	if err != nil {
		s.err = err
		return *ZeroInt
	}
```

**File:** x/evm/state/balance.go (L154-159)
```go
func (s *DBImpl) send(from sdk.AccAddress, to sdk.AccAddress, amt *big.Int) {
	usei, wei := SplitUseiWeiAmount(amt)
	err := s.k.BankKeeper().SendCoinsAndWei(s.ctx, from, to, usei, wei)
	if err != nil {
		s.err = err
	}
```

**File:** precompiles/bank/legacy/v630/bank.go (L216-223)
```go
	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, 0, err
	}
```
