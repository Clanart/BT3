Confirmed: `SendCoins`/`InputOutputCoins` in the Cosmos bank keeper enforce `IsSendEnabledCoins` (send-enabled/pause checks) and `BlockedAddr` on the recipient, but `AddCoins`/`SubUnlockedCoins` themselves do not call `IsSendEnabledCoin`/`BlockedAddr` (only `CanSendTo`) [1](#0-0) . The EVM StateDB's `SubBalance`/`AddBalance` — invoked on every EVM value-transfer (plain `CALL` with `value`, or precompile flows) — call `BankKeeper().SubUnlockedCoins` / `BankKeeper().AddCoins` directly, never `SendCoins`, so they never run the `IsSendEnabledCoins` check [2](#0-1) [3](#0-2) .

### Title
Bank module `send_enabled`/pause governance parameter is not enforced on EVM native (usei/wei) value transfers - ([File: x/evm/state/balance.go])

### Summary
The Cosmos SDK bank module's `SendEnabled`/`DefaultSendEnabled` params (the chain's transfer-pause mechanism, analogous to `Pausable` in the reported FootiumClub bug) are enforced only in `SendCoins`/`sendCoinsWithoutAccCreation`/`InputOutputCoins` via `IsSendEnabledCoins` [4](#0-3) . EVM-side balance movement — every plain-value `CALL`/`transferFrom`/precompile settlement — goes through `DBImpl.SubBalance`/`AddBalance`, which call `SubUnlockedCoins`/`AddCoins` directly and skip the `IsSendEnabledCoins` check entirely [5](#0-4) [6](#0-5) .

### Finding Description
`SendCoinsWithoutAccCreation` calls `SubUnlockedCoins` and `AddCoins` internally but is reached only through the `SendCoins`/`InputOutputCoins` cosmos-msg paths [7](#0-6) . That is the only call site where `IsSendEnabledCoins` would be checked as part of a `BankCoinTransferrer`-style flow used by e.g. wasmd's `TransferCoins` [8](#0-7) . However, the EVM `StateDB.SubBalance`/`AddBalance` implementation — used for every native `usei`/`wei` transfer inside EVM execution (plain value transfers, `bank` precompile's `sendNative`, fee refunds, etc.) — calls `BankKeeper().SubUnlockedCoins` and `BankKeeper().AddCoins` directly, bypassing `SendCoins` and therefore bypassing `IsSendEnabledCoins` [9](#0-8) . `AddCoins` still checks `CanSendTo` (recipient checkers) but never `IsSendEnabledCoin`/`IsSendEnabledCoins` [10](#0-9) . `BlockedAddr` is likewise only invoked from the higher-level `BankCoinTransferrer`/message-server layers, not from `AddCoins`/`SubUnlockedCoins`. Consequently, if an operator sets `DefaultSendEnabled = false` (or disables `usei` specifically) as an emergency pause — e.g., in response to an incident, mirroring the intent of `Pausable` in the reported bug — any EVM transaction moving native value (an ordinary `to.call{value: x}()`, a Solidity `transfer`, or the `bank` precompile's `sendNative`) still succeeds, because it never routes through `SendCoins`/`IsSendEnabledCoins`.

### Impact Explanation
An emergency pause of native token transfers via bank module governance params is a documented protocol control [11](#0-10) . This control fails silently for the EVM lane: any unprivileged EVM caller can still move `usei`/`wei` value between EVM-associated accounts while the chain believes transfers are paused. This is a partial bypass of a security control intended to halt fund movement during an incident (e.g., to stop draining of compromised funds), directly undermining the incident-response capability the pause is meant to provide, matching the impact class of the referenced bug (pause bypass enabling continued fund movement).

### Likelihood Explanation
Any address with an EVM/Sei address association can trigger this at any time by sending a plain value-transfer transaction or invoking `bank.sendNative` — no special privilege is required, and the bypass is deterministic and always reachable whenever `SendEnabled` is toggled to `false` for `usei` (or default-disabled). This makes the likelihood high whenever operators actually rely on this control.

### Recommendation
Enforce `IsSendEnabledCoins` (and ideally `BlockedAddr`) checks inside `DBImpl.SubBalance`/`AddBalance` (or a shared choke point they both call) for the base denom, or explicitly route EVM native transfers through the same `SendCoins`-equivalent guarded path used by Cosmos message handling, so that a bank-level pause uniformly halts both Cosmos and EVM native transfers.

### Proof of Concept
1. Governance/admin sets bank params `DefaultSendEnabled = false` (or `SendEnabled: [{Denom: "usei", Enabled: false}]`), intending to pause all native transfers chain-wide.
2. Attempt a Cosmos `MsgSend` of `usei` — fails with `ErrSendDisabled` because `SendCoins` calls `IsSendEnabledCoins` [4](#0-3) .
3. From an EVM account with an associated Sei address, send an EVM transaction with non-zero `value` to another EVM-associated address (a plain `to.call{value: N}()` or `bank.sendNative`). This routes to `StateDB.SubBalance`/`AddBalance`, which call `BankKeeper().SubUnlockedCoins`/`AddCoins` directly [5](#0-4)  — no `IsSendEnabledCoins` check occurs, and the transfer succeeds despite the pause.

### Citations

**File:** sei-cosmos/x/bank/keeper/send.go (L162-230)
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

// recordNewAccounts dual-emits the legacy new-account counter and its OTel
// counterpart (bank_new_account). Runs from consensus-critical send paths, so
// a telemetry fault here must not panic into the caller.
func recordNewAccounts(ctx context.Context, count int64) {
	if count <= 0 {
		return
	}
	defer func() {
		if e := recover(); e != nil {
			fmt.Fprintf(os.Stderr, "telemetry panic: %v\n%s", e, debug.Stack())
		}
	}()
	// TODO(PLT-353): remove once bank_new_account verified
	telemetry.IncrCounter(float32(count), "new", "account")
	bankMetrics.newAccount.Add(ctx, count)
}

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

**File:** sei-cosmos/x/bank/keeper/send.go (L274-292)
```go
// AddCoins increase the addr balance by the given amt. Fails if the provided amt is invalid.
// It emits a coin received event.
func (k BaseSendKeeper) AddCoins(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Coins, checkNeg bool) error {
	if !k.CanSendTo(ctx, addr) {
		return sdkerrors.ErrInvalidRecipient
	}
	if !amt.IsValid() {
		return sdkerrors.Wrap(sdkerrors.ErrInvalidCoins, amt.String())
	}

	for _, coin := range amt {
		balance := k.GetBalance(ctx, addr, coin.Denom)
		newBalance := balance.Add(coin)

		err := k.setBalance(ctx, addr, newBalance, checkNeg)
		if err != nil {
			return err
		}
	}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L355-365)
```go
// IsSendEnabledCoins checks the coins provide and returns an ErrSendDisabled if
// any of the coins are not configured for sending.  Returns nil if sending is enabled
// for all provided coin
func (k BaseSendKeeper) IsSendEnabledCoins(ctx sdk.Context, coins ...sdk.Coin) error {
	for _, coin := range coins {
		if !k.IsSendEnabledCoin(ctx, coin) {
			return sdkerrors.Wrapf(types.ErrSendDisabled, "%s transfers are currently disabled", coin.Denom)
		}
	}
	return nil
}
```

**File:** x/evm/state/balance.go (L15-95)
```go
func (s *DBImpl) SubBalance(evmAddr common.Address, amtUint256 *uint256.Int, reason tracing.BalanceChangeReason) uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, evmAddr)
	amt := amtUint256.ToBig()
	if amt.Sign() == 0 {
		return *ZeroInt
	}
	if amt.Sign() < 0 {
		return s.AddBalance(evmAddr, new(uint256.Int).Neg(amtUint256), reason)
	}

	ctx := s.ctx
	var oldBalance *uint256.Int
	if s.logger != nil && s.logger.OnBalanceChange != nil {
		oldBalance = s.GetBalance(evmAddr)
	}

	// this avoids emitting cosmos events for ephemeral bookkeeping transfers like send_native
	if s.eventsSuppressed {
		ctx = ctx.WithEventManager(sdk.NewEventManager())
	}

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

	if s.logger != nil && s.logger.OnBalanceChange != nil && oldBalance != nil {
		newBalance := s.GetBalance(evmAddr).ToBig()
		oldBalance := new(big.Int).Add(newBalance, amt)
		s.logger.OnBalanceChange(evmAddr, oldBalance, newBalance, reason)
	}

	surplus := sdk.NewIntFromBigInt(amt)
	s.tempState.surplus = s.tempState.surplus.Add(surplus)
	s.journal = append(s.journal, &surplusChange{delta: surplus})
	return *ZeroInt
}

func (s *DBImpl) AddBalance(evmAddr common.Address, amtUint256 *uint256.Int, reason tracing.BalanceChangeReason) uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, evmAddr)
	amt := amtUint256.ToBig()
	if amt.Sign() == 0 {
		return *ZeroInt
	}
	if amt.Sign() < 0 {
		return s.SubBalance(evmAddr, new(uint256.Int).Neg(amtUint256), reason)
	}

	ctx := s.ctx
	var oldBalance *uint256.Int
	if s.logger != nil && s.logger.OnBalanceChange != nil {
		oldBalance = s.GetBalance(evmAddr)
	}
	// this avoids emitting cosmos events for ephemeral bookkeeping transfers like send_native
	if s.eventsSuppressed {
		ctx = ctx.WithEventManager(sdk.NewEventManager())
	}

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

**File:** sei-cosmos/x/bank/spec/05_params.md (L1-24)
```markdown
<!--
order: 5
-->

# Parameters

The bank module contains the following parameters:

| Key                | Type          | Example                            |
| ------------------ | ------------- | ---------------------------------- |
| SendEnabled        | []SendEnabled | [{denom: "usei", enabled: true }] |
| DefaultSendEnabled | bool          | true                               |

## SendEnabled

The send enabled parameter is an array of SendEnabled entries mapping coin
denominations to their send_enabled status. Entries in this list take
precedence over the `DefaultSendEnabled` setting.

## DefaultSendEnabled

The default send enabled value controls send transfer capability for all
coin denominations unless specifically included in the array of `SendEnabled`
parameters.
```
