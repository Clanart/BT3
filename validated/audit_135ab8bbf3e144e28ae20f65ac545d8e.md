### Title
`SendEnabled` pause flag is not enforced on module-inbound bank transfers (`SendCoinsFromAccountToModule` / `SendCoinsFromModuleToAccount`) - ([File: sei-cosmos/x/bank/keeper/keeper.go], [File: sei-cosmos/x/gov/keeper/deposit.go], [File: x/tokenfactory/keeper/bankactions.go])

### Summary
sei-chain implements a `SendEnabled` pause mechanism for `x/bank`, checked via `IsSendEnabledCoins`, that operators use to halt transfers of a specific denom (e.g. in response to an oracle malfunction, exploit, or depeg event). However, exactly like the `IronBank.sol` report — where the borrow-market pause flag was checked in some code paths (`isMarketSeizable`) but not others (`liquidate`) — sei-chain's pause flag is enforced only in the direct `MsgSend` path [1](#0-0) , but is skipped entirely by `BaseKeeper.SendCoinsFromAccountToModule` and `SendCoinsFromModuleToAccount`, which are reachable from ordinary user transactions through other modules (gov deposits, tokenfactory burn, etc.) [2](#0-1) .

### Finding Description
`IsSendEnabledCoins` is the pause check for a denom's transferability [3](#0-2) . It is invoked in the bank module's own `MsgSend` handler [1](#0-0) , and by wasm's `BankCoinTransferrer.TransferCoins` used for CosmWasm-initiated sends [4](#0-3) .

However, `SendCoinsFromAccountToModule` (an unprivileged-user-to-module transfer) and `SendCoinsFromModuleToAccount` (module-to-user transfer) never call `IsSendEnabledCoins`: [5](#0-4) 

These two entry points are reachable by any transaction sender through several modules that a normal user calls directly:
- `x/gov`'s `AddDeposit`, invoked on every `MsgDeposit`/`MsgSubmitProposal`, moves the depositor's coins into the gov module account via `SendCoinsFromAccountToModule` with no send-enabled check [6](#0-5) , and `RefundDeposits` later pays them back out via `SendCoinsFromModuleToAccount`, again without a send-enabled check [7](#0-6) .
- `x/tokenfactory`'s `burnFrom`, invoked on every `MsgBurn`, moves the target account's coins into the tokenfactory module account via `SendCoinsFromAccountToModule` without any send-enabled check, and its counterpart `mintTo` pays coins back out via `SendCoinsFromModuleToAccount`, likewise unchecked [8](#0-7) .

If a denom's `SendEnabled` flag is set to `false` — the intended pause mechanism, exactly analogous to Iron Bank's transfer-pause flag used to halt a market during an oracle incident or exploit — any account can still move that denom by depositing/withdrawing it through gov or by burning/minting it through tokenfactory, because those code paths call the lower-level `SendCoinsFromAccountToModule`/`SendCoinsFromModuleToAccount` functions that skip the pause check entirely.

### Impact Explanation
This is analogous to the reported bug class: a safety pause that is supposed to halt transfer/movement of a denom (e.g., during a security incident, oracle malfunction, or exploit of that specific denom) can be bypassed by routing the same economic transfer through gov deposits or tokenfactory mint/burn, both of which are reachable from any unprivileged transaction sender. Depending on how the `SendEnabled` pause is used operationally (e.g., freezing a compromised or depegged token to limit damage), this bypass allows continued movement of the paused asset and undermines the intended halt, which could facilitate fund loss or an unintended supply/accounting mismatch for the paused denom during an incident window.

### Likelihood Explanation
Likelihood is moderate: the bypass requires only standard `MsgDeposit`/`MsgSubmitProposal` (gov) or `MsgBurn`/`MsgMint` (tokenfactory) transactions with no special privileges, and is deterministically reproducible whenever an operator disables `SendEnabled` for a factory-created denom while relying on it to fully halt transfers of that denom.

### Recommendation
Add an `IsSendEnabledCoins` check (or an explicit pause-aware guard) to `BaseKeeper.SendCoinsFromAccountToModule` and `SendCoinsFromModuleToAccount` in `sei-cosmos/x/bank/keeper/keeper.go`, mirroring the check already done in `msg_server.go`'s `Send` handler, so that all user-initiated code paths that move a paused denom into or out of a module account are consistently blocked, closing the gap the same way the Iron Bank report recommends adding a consistent pause check across all liquidation-adjacent paths.

### Proof of Concept
1. Operator sets `SendEnabled=false` for `factory/creator/mydenom` via governance param update, intending to freeze all movement of the compromised/depegged denom.
2. Attacker (or any user) still holding `mydenom` submits `MsgDeposit` on any active governance proposal with `mydenom` as the deposit amount. `AddDeposit` calls `SendCoinsFromAccountToModule`, which succeeds because it never calls `IsSendEnabledCoins`, moving the "paused" tokens into the gov module account.
3. Attacker then withdraws the proposal deposit (or waits for `RefundDeposits` on proposal completion), which calls `SendCoinsFromModuleToAccount` — again without any send-enabled check — returning the coins, effectively laundering a transfer of the paused denom between two addresses (depositor and the address that receives the refund, which can be a different account if using vote-weight-linked deposit flows) despite the pause.
4. Alternatively, the same bypass is achievable via `MsgBurn`/`MsgMint` in `x/tokenfactory`, where `burnFrom`/`mintTo` move the paused denom through the module account without ever consulting `SendEnabled`.

### Citations

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-31)
```go
func (k msgServer) Send(goCtx context.Context, msg *types.MsgSend) (*types.MsgSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	if err := k.IsSendEnabledCoins(ctx, msg.Amount...); err != nil {
		return nil, err
	}
```

**File:** sei-cosmos/x/bank/keeper/keeper.go (L386-427)
```go
// SendCoinsFromModuleToAccount transfers coins from a ModuleAccount to an AccAddress.
// It will panic if the module account does not exist. An error is returned if
// the recipient address is black-listed or if sending the tokens fails.
func (k BaseKeeper) SendCoinsFromModuleToAccount(
	ctx sdk.Context, senderModule string, recipientAddr sdk.AccAddress, amt sdk.Coins,
) error {

	senderAddr := k.ak.GetModuleAddress(senderModule)
	if senderAddr == nil {
		panic(sdkerrors.Wrapf(sdkerrors.ErrUnknownAddress, "module account %s does not exist", senderModule))
	}

	if k.BlockedAddr(recipientAddr) {
		return sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", recipientAddr)
	}
	return k.SendCoins(ctx, senderAddr, recipientAddr, amt)
}

// SendCoinsFromModuleToModule transfers coins from a ModuleAccount to another.
// It will panic if either module account does not exist.
func (k BaseKeeper) SendCoinsFromModuleToModule(
	ctx sdk.Context, senderModule, recipientModule string, amt sdk.Coins,
) error {

	senderAddr := k.ak.GetModuleAddress(senderModule)
	if senderAddr == nil {
		panic(sdkerrors.Wrapf(sdkerrors.ErrUnknownAddress, "module account %s does not exist", senderModule))
	}

	recipientAcc := k.ak.GetModuleAccount(ctx, recipientModule)
	if recipientAcc == nil {
		panic(sdkerrors.Wrapf(sdkerrors.ErrUnknownAddress, "module account %s does not exist", recipientModule))
	}

	if amt.IsZero() {
		return nil
	}

	logger.Debug("Sending coins from module to module", "sender", senderModule, "sender_address", senderAddr.String(), "recipient", recipientModule, "recipient_address", recipientAcc.GetAddress().String(), "amount", amt.String())

	return k.SendCoins(ctx, senderAddr, recipientAcc.GetAddress(), amt)
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L355-362)
```go
// IsSendEnabledCoins checks the coins provide and returns an ErrSendDisabled if
// any of the coins are not configured for sending.  Returns nil if sending is enabled
// for all provided coin
func (k BaseSendKeeper) IsSendEnabledCoins(ctx sdk.Context, coins ...sdk.Coin) error {
	for _, coin := range coins {
		if !k.IsSendEnabledCoin(ctx, coin) {
			return sdkerrors.Wrapf(types.ErrSendDisabled, "%s transfers are currently disabled", coin.Denom)
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

**File:** sei-cosmos/x/gov/keeper/deposit.go (L126-129)
```go
	err := keeper.bankKeeper.SendCoinsFromAccountToModule(ctx, depositorAddr, types.ModuleName, depositAmount)
	if err != nil {
		return false, err
	}
```

**File:** sei-cosmos/x/gov/keeper/deposit.go (L184-187)
```go
		err := keeper.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, depositor, deposit.Amount)
		if err != nil {
			panic(err)
		}
```

**File:** x/tokenfactory/keeper/bankactions.go (L11-58)
```go
func (k Keeper) mintTo(ctx sdk.Context, amount sdk.Coin, mintTo string) error {
	// verify that denom is an x/tokenfactory denom
	_, _, err := types.DeconstructDenom(amount.Denom)
	if err != nil {
		return err
	}

	logger.Info("Minting amount for module", "amount", amount, "module", types.ModuleName)
	err = k.bankKeeper.MintCoins(ctx, types.ModuleName, sdk.NewCoins(amount))
	if err != nil {
		return err
	}

	addr, err := sdk.AccAddressFromBech32(mintTo)
	if err != nil {
		return err
	}

	logger.Info("Sending minted amount to addr", "amount", amount, "addr", addr)
	return k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName,
		addr,
		sdk.NewCoins(amount))
}

func (k Keeper) burnFrom(ctx sdk.Context, amount sdk.Coin, burnFrom string) error {
	// verify that denom is an x/tokenfactory denom
	_, _, err := types.DeconstructDenom(amount.Denom)
	if err != nil {
		return err
	}

	addr, err := sdk.AccAddressFromBech32(burnFrom)
	if err != nil {
		return err
	}

	logger.Info("Sending amount to module from account", "amount", amount, "module", types.ModuleName, "account", addr)
	err = k.bankKeeper.SendCoinsFromAccountToModule(ctx,
		addr,
		types.ModuleName,
		sdk.NewCoins(amount))
	if err != nil {
		return err
	}

	logger.Info("Burning amount from module", "amount", amount, "module", types.ModuleName)
	return k.bankKeeper.BurnCoins(ctx, types.ModuleName, sdk.NewCoins(amount))
}
```
