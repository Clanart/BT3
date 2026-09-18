### Title
Tokenfactory denom allow-list restriction is not enforced on `MultiSend` (`InputOutputCoins`) - (File: sei-cosmos/x/bank/keeper/send.go)

### Summary
The `x/tokenfactory` denom allow-list feature restricts which addresses may send a given tokenfactory denom by calling `IsInDenomAllowList` from the `SendKeeper` before a transfer is permitted. This limitation is enforced in the single-transfer `SendCoins` path but is not applied in `InputOutputCoins`, the keeper method backing the bank module's `MultiSend` message. An unprivileged transaction sender can therefore move an allow-listed tokenfactory denom to/from addresses that are not on the denom's allow list simply by using `MultiSend` instead of `Send`, exactly mirroring the eZ Publish advisory pattern where an access/object-state limitation exists in code but has no effect on a reachable path.

### Finding Description
`BaseSendKeeper` declares `IsInDenomAllowList(ctx, addr, coins, cache) bool` as part of the `SendKeeper` interface and implements it to gate transfers of any denom prefixed with `factory` (`TokenFactoryPrefix`) against a per-denom allow list [1](#0-0) . The equivalent logic (identical semantics) is documented in the parallel `giga` implementation: "the address is allowed to send the coins only if it is in the allow list" [2](#0-1) .

`InputOutputCoins`, which implements the bank module's `MultiSend` message handling, subtracts coins from each input via `SubUnlockedCoins` and credits each output via `AddCoins`, but never calls `IsInDenomAllowList` (or any allow-list check) on either the input or output addresses [3](#0-2) . This means the allow-list restriction — a policy explicitly designed to limit which accounts can hold/move a given tokenfactory denom — has no effect when a user submits a `MultiSend` transaction instead of a `Send` transaction.

### Impact Explanation
Tokenfactory denom allow lists are a supply/authority control: a denom admin can restrict circulation of their token to a specific set of addresses (e.g., for compliance, vesting, or permissioned-asset designs). Because `MultiSend` silently bypasses this restriction, any unprivileged transaction sender holding a restricted denom can transfer it to or from any address, completely defeating the allow-list guarantee that other modules/integrators rely on. This is a fund-movement/authority-bypass issue affecting bank/tokenfactory access control, analogous in class to the referenced advisory where a limitation check existed but was rendered ineffective on a reachable path.

### Likelihood Explanation
The bypass requires no special privilege — any account holding the restricted denom can send a standard Cosmos SDK `MsgMultiSend` transaction, which is a public, always-enabled message type in the bank module. No contract deployment, precompile access, or governance action is needed, making this trivially reachable by any transaction sender.

### Recommendation
Add an `IsInDenomAllowList` check (mirroring the one used in `SendCoins`) inside `InputOutputCoins` for both `Input` and `Output` addresses before subtracting/adding coins, so `MultiSend` enforces the same tokenfactory denom allow-list restriction as `Send`.

### Proof of Concept
1. Denom admin creates a tokenfactory denom `factory/<creator>/restricted` and sets a `DenomAllowList` containing only address `A`.
2. Address `A` holds `100 factory/<creator>/restricted`. Address `B` is not in the allow list.
3. `A` attempts `MsgSend` of the denom to `B` — this is rejected by `IsInDenomAllowList` in `SendCoins`.
4. `A` instead submits `MsgMultiSend` with input `{A, 100 factory/.../restricted}` and output `{B, 100 factory/.../restricted}`.
5. `InputOutputCoins` processes the transfer via `SubUnlockedCoins`/`AddCoins` without any allow-list check, so the transfer succeeds and `B` (not on the allow list) now holds the restricted denom, bypassing the intended limitation [3](#0-2) .

### Citations

**File:** sei-cosmos/x/bank/keeper/send.go (L43-52)
```go
	IsSendEnabledCoin(ctx sdk.Context, coin sdk.Coin) bool
	IsSendEnabledCoins(ctx sdk.Context, coins ...sdk.Coin) error
	SetDenomAllowList(ctx sdk.Context, denom string, allowList types.AllowList)
	GetDenomAllowList(ctx sdk.Context, denom string) types.AllowList
	IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool

	BlockedAddr(addr sdk.AccAddress) bool
	RegisterRecipientChecker(RecipientChecker)
	CanSendTo(ctx sdk.Context, recipient sdk.AccAddress) bool
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L103-157)
```go
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
```

**File:** giga/deps/xbank/keeper/send.go (L479-502)
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
