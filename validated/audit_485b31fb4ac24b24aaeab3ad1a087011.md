Based on my research, I found a strong analog in the `sei-cosmos/x/gov` deposit refund path, which is structurally very similar to the reported bug: an attacker funds a payout pool with an attacker-controlled token whose transfer can be made to fail, and the failure isn't handled gracefully — but in sei-chain the failure mode is worse (a `panic` instead of a mere DoS).

### Title
Malicious tokenfactory-denom governance deposit can panic `RefundDeposits` in `x/gov` EndBlocker and halt the chain - (File: `sei-cosmos/x/gov/keeper/deposit.go`)

### Summary
`MsgDeposit.ValidateBasic` places no restriction on which denom(s) a depositor uses to fund a governance proposal — any valid, non-negative `sdk.Coins` is accepted [1](#0-0) . Since anyone can create a `tokenfactory` denom and later restrict who can send/receive it via a denom allow list [2](#0-1) , an attacker can deposit their own restricted tokenfactory coin into a proposal. When the deposit period ends and refunds are processed in `RefundDeposits`, the module-to-account send for that coin can fail, and the code `panic`s on that error rather than handling it — mirroring the OpenQ report's pattern of "malicious token attached to a payout, blocking all other payouts," except here it crashes the validator/EndBlocker instead of merely blocking a claim.

### Finding Description
`AddDeposit` unconditionally calls `SendCoinsFromAccountToModule` for whatever coins are supplied in `MsgDeposit`, with no denom allowlist/consensus vetting of which tokens can be deposited [3](#0-2) . The deposit is later refunded via `RefundDeposits`, which iterates all deposits for a proposal: [4](#0-3) 

The function does check `BlockedAddr`/`CanSendTo` for the *recipient address* and skips (rather than errors) in that case, but it does **not** account for per-denom restrictions such as the tokenfactory `AllowList` feature, which independently blocks sends of a specific denom to/from non-allowed addresses via `IsInDenomAllowList` [5](#0-4) . This allow list is attacker-controlled: as the tokenfactory denom's creator/admin, the attacker can call `SetDenomAllowList` to restrict the very address they used to deposit, either before or after depositing, so that when `SendCoinsFromModuleToAccount` is invoked during refund, the transfer fails for that denom, and the code executes `panic(err)`.

### Impact Explanation
A `panic` inside `RefundDeposits`, which runs from the governance module's `EndBlocker` (via `abci.go`/`proposal.go`), is not a normal transaction-level revert — it aborts block processing entirely rather than just failing one deposit's refund. This exceeds "payout is blocked" from the original report and instead risks a **full validator/chain halt** at the block where the proposal's deposit/voting period ends, since every honest node executing `EndBlock` would hit the same panic deterministically (or, depending on recovery middleware, could still corrupt/skip proposal cleanup).

### Likelihood Explanation
Any unprivileged user can: (1) create a tokenfactory denom (`MsgCreateDenom`), (2) deposit a nominal amount of it into any active governance proposal via `MsgDeposit` — no minimum-deposit-denom restriction applies to `MsgDeposit.ValidateBasic` — and (3) set (or later modify) that denom's `AllowList` to exclude their own depositor address. This requires no special privileges beyond being able to create a tokenfactory denom and submit a deposit, both of which are permissionless.

### Recommendation
In `RefundDeposits`, do not `panic` on a `SendCoinsFromModuleToAccount` error. Instead, treat the failure the same way the function already treats a `BlockedAddr`/`CanSendTo` failure: skip the refund, retain the deposit record backed by the module balance, and continue processing the remaining deposits, emitting an event/log for operators to handle out-of-band. Additionally, consider disallowing tokenfactory-denom-with-allowlist coins as valid gov deposit denoms, or snapshotting/validating denom sendability at deposit time.

### Proof of Concept
Conceptual outline (cannot be executed within this read-only analysis):
1. Attacker submits `MsgCreateDenom` to create `factory/<attacker>/evil`.
2. Attacker mints some `factory/<attacker>/evil` to themselves and submits `MsgDeposit` for an active proposal using this denom (passes `ValidateBasic`, `AddDeposit` succeeds since it doesn't check denom acceptability beyond `IsValid`).
3. Attacker calls `SetDenomAllowList` (via the tokenfactory module) on `factory/<attacker>/evil`, adding an allow list that excludes the attacker's own address (or a list that never includes it).
4. When the proposal's deposit/voting period ends, `EndBlocker` invokes `RefundDeposits`, which calls `SendCoinsFromModuleToAccount` for the attacker's deposit; the send fails the denom allow-list check.
5. The code hits `panic(err)`, aborting `EndBlock` for that height on every node.

I was not able to fully trace, within the tool budget, the exact internal call from `SendCoinsFromModuleToAccount` down to `IsInDenomAllowList` (I only confirmed the function is referenced in `sei-cosmos/x/bank/keeper/send.go` and `msg_server.go`, not the exact call site inside the module-to-account path). This should be verified directly against `sei-cosmos/x/bank/keeper/send.go`'s `SendCoins`/`sendCoins` internals before treating this as fully confirmed; if `SendCoinsFromModuleToAccount` bypasses `IsInDenomAllowList` entirely, an alternate attacker-controlled failure mode (e.g., a denom marked as `IsSendEnabledCoin(false)` after deposit, if mutable) should be checked instead.

### Citations

**File:** sei-cosmos/x/gov/types/msgs.go (L150-163)
```go
// ValidateBasic implements Msg
func (msg MsgDeposit) ValidateBasic() error {
	if msg.Depositor == "" {
		return sdkerrors.Wrap(sdkerrors.ErrInvalidAddress, msg.Depositor)
	}
	if !msg.Amount.IsValid() {
		return sdkerrors.Wrap(sdkerrors.ErrInvalidCoins, msg.Amount.String())
	}
	if msg.Amount.IsAnyNegative() {
		return sdkerrors.Wrap(sdkerrors.ErrInvalidCoins, msg.Amount.String())
	}

	return nil
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L20-22)
```go
const (
	TokenFactoryPrefix = "factory"
)
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

**File:** sei-cosmos/x/gov/keeper/deposit.go (L106-129)
```go
// AddDeposit adds or updates a deposit of a specific depositor on a specific proposal
// Activates voting period when appropriate
func (keeper Keeper) AddDeposit(ctx sdk.Context, proposalID uint64, depositorAddr sdk.AccAddress, depositAmount sdk.Coins) (bool, error) {
	// Checks to see if proposal exists
	proposal, ok := keeper.GetProposal(ctx, proposalID)
	if !ok {
		return false, sdkerrors.Wrapf(types.ErrUnknownProposal, "%d", proposalID)
	}

	// Check if proposal is still depositable
	if (proposal.Status != types.StatusDepositPeriod) && (proposal.Status != types.StatusVotingPeriod) {
		return false, sdkerrors.Wrapf(types.ErrInactiveProposal, "%d", proposalID)
	}
	if keeper.IncrementalTallyEnabled(ctx) && proposal.Status == types.StatusVotingPeriod {
		if proposal.VotingEndTime.Before(ctx.BlockTime()) || keeper.voteDelegationSnapshotFrozen(ctx, proposal) {
			return false, sdkerrors.Wrapf(types.ErrInactiveProposal, "%d", proposalID)
		}
	}

	// update the governance module's account coins pool
	err := keeper.bankKeeper.SendCoinsFromAccountToModule(ctx, depositorAddr, types.ModuleName, depositAmount)
	if err != nil {
		return false, err
	}
```

**File:** sei-cosmos/x/gov/keeper/deposit.go (L169-192)
```go
// RefundDeposits refunds deposits whose recipients can receive funds and deletes
// their records. Deposits for unpayable recipients remain recorded and backed by
// the governance module balance.
func (keeper Keeper) RefundDeposits(ctx sdk.Context, proposalID uint64) {
	store := ctx.KVStore(keeper.storeKey)

	keeper.IterateDeposits(ctx, proposalID, func(deposit types.Deposit) bool {
		depositor := sdk.MustAccAddressFromBech32(deposit.Depositor)
		if keeper.bankKeeper.BlockedAddr(depositor) || !keeper.bankKeeper.CanSendTo(ctx, depositor) {
			// Retain the deposit so its record continues to account for the backing
			// module balance. Recovering a permanently unreceivable deposit requires
			// a migration.
			return false
		}

		err := keeper.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, depositor, deposit.Amount)
		if err != nil {
			panic(err)
		}

		store.Delete(types.DepositKey(proposalID, depositor))
		return false
	})
}
```
