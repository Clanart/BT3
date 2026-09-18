### Title
Gov proposal deposits become permanently locked in the gov module account if the depositor is later blocked or denylisted before refund - ([File: sei-cosmos/x/gov/keeper/deposit.go])

### Summary
`RefundDeposits` in the `x/gov` module refunds deposits back to depositors when a proposal concludes voting, but if the depositor address fails the `BlockedAddr`/`CanSendTo` checks at refund time, the deposit record and backing coins are silently retained in the gov module account forever, with no on-chain mechanism to recover or redirect them. This mirrors the referenced Y2K `ControllerPeggedAssetV2` bug class: funds accumulate in a contract/module-controlled account for a special-case code path (there, `NullEpoch`; here, an unpayable-recipient refund) with no sweep-back logic.

### Finding Description
`AddDeposit` lets any account submit `MsgDeposit` and moves usei/tokenfactory coins from the depositor into the `gov` module account via `SendCoinsFromAccountToModule`. [1](#0-0) 

When the proposal's voting period ends (normal path), `RefundDeposits` iterates all deposits and attempts to pay them back to the original depositor address. If `keeper.bankKeeper.BlockedAddr(depositor)` or `!keeper.bankKeeper.CanSendTo(ctx, depositor)` evaluates true, the function explicitly skips the refund and **keeps the deposit record and backing coins in the module account indefinitely**, per its own doc comment: "Deposits for unpayable recipients remain recorded and backed by the governance module balance... Recovering a permanently unreceivable deposit requires a migration." [2](#0-1) 

`CanSendTo` is driven by `RegisterRecipientChecker` callbacks, one of which enforces the tokenfactory/bank `DenomAllowList` (`IsInDenomAllowList`) for `factory/...` denoms. [3](#0-2) 

This produces a concrete, transaction-reachable freeze scenario:
1. A user deposits a tokenfactory (`factory/...`) denom coin into a governance proposal via `MsgDeposit` while no allow-list restricts them.
2. Before the proposal's voting period ends, the tokenfactory denom's authority (an on-chain, message-reachable action — `SetDenomAllowList`) adds or changes an allow-list for that denom that excludes the depositor's address, or the depositor's address otherwise becomes blocked (e.g., a module address is later associated, or governance blocks the address via bank params).
3. When the voting period ends and `RefundDeposits` executes, `CanSendTo`/`BlockedAddr` now returns a value that skips the refund path — the deposit remains in the `gov` module account forever, with `store.Delete` never being called for it and `SendCoinsFromModuleToAccount` never invoked.
4. There is no privileged or unprivileged message to sweep leftover unpayable deposits from the gov module account; the only remediation acknowledged in the code is a chain "migration."

This is functionally identical to the referenced bug class: control-flow branches that skip the "send funds back" step leave value permanently stuck in a module-controlled account with no path back to users or treasury.

### Impact Explanation
Coins deposited on governance proposals can become permanently and unrecoverably locked in the `gov` module account whenever the depositor becomes unable to receive funds (via tokenfactory allow-list changes or blocked-address status) between deposit and refund time. This is a genuine, non-privileged-reachable fund freezing bug matching the "permanent freezing of funds" acceptance criterion. The affected funds are real user assets (potentially large sums for high-value proposals) and the code itself documents that no on-chain recovery exists.

### Likelihood Explanation
Reaching this state requires: (a) a normal `MsgDeposit` from any account (trivially reachable, unprivileged), and (b) the depositor's address becoming untransferable for the deposited denom before refund — which can happen through legitimate, message-driven tokenfactory allow-list updates by the denom's admin, or other blocked-address transitions. Because tokenfactory denom admins can freely update allow-lists at any time via a standard message, and governance voting periods can span days, the race window is realistic, making this a plausible (not purely theoretical) scenario, albeit dependent on a tokenfactory admin's action.

### Recommendation
Add a bounded on-chain recovery path for unrefundable deposits, e.g.:
- On refund failure, redirect the coins to the community pool (similar to how `distribution`'s `RefundDeposits`-adjacent flows or dust rounding is sent to the community pool) instead of leaving them attributed to an unreachable depositor.
- Alternatively, provide a permissionless "claim/re-refund" message that re-attempts the transfer once the address becomes payable again, or a governance-gated sweep message for deposits stuck longer than a configurable period.
- At minimum, emit a distinct event/queryable state so operators are alerted to bootstrap a migration promptly, and document the invariant this creates for total-supply-vs-controlled-funds accounting.

### Proof of Concept
Conceptual reproduction (cannot be fully executed without a live chain, but derivable from the code above):
1. Submit `MsgSubmitProposal` and `MsgDeposit` from account `A` using `factory/<creator>/<denom>` coins with no allow-list set (so `CanSendTo` currently returns true).
2. As the tokenfactory denom's admin, submit a message to set `SetDenomAllowList` for that denom to a list that excludes `A`.
3. Let the proposal's voting period end so `x/gov`'s `EndBlocker` calls `RefundDeposits(ctx, proposalID)`.
4. Observe: `keeper.bankKeeper.CanSendTo(ctx, A)` now returns `false` because `A` is not present in the tokenfactory allow-list [4](#0-3) , so `RefundDeposits` returns early for `A`'s deposit record, per the logic at [5](#0-4) , leaving the coins in the `gov` module account with no further code path to release them.

**Note on uncertainty**: I could not fully verify from the index which `RecipientChecker`s are registered by the app wiring (i.e., whether the tokenfactory allow-list checker or any blocked-address checker is actually registered against `CanSendTo`/`BlockedAddr` in `x/gov`'s specific deposit-refund invocation, versus only used in generic `SendCoins` paths). The `deposit.go` comment strongly suggests this is a recognized, intentional trade-off already baked into the code (i.e., the team is aware and considers a migration the acceptable remedy), which may mean this is a known/accepted design limitation rather than an unrecognized vulnerability. Confirming the exact registered checkers and any existing governance-level mitigation would require reviewing `app.go`'s bank-keeper wiring, which is not fully available in the index — a full Devin session with repository access would be needed to confirm the exact set of `RegisterRecipientChecker` callbacks in production and whether any off-chain/governance sweep tooling already exists.

### Citations

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

**File:** sei-cosmos/x/gov/keeper/deposit.go (L169-191)
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
```

**File:** sei-cosmos/x/bank/keeper/send.go (L465-524)
```go
func (k BaseSendKeeper) CanSendTo(ctx sdk.Context, recipient sdk.AccAddress) bool {
	for _, rc := range *k.recipientCheckers {
		if !rc(ctx, recipient) {
			return false
		}
	}
	return true
}

func SplitUseiWeiAmount(amt sdk.Int) (sdk.Int, sdk.Int) {
	return amt.Quo(OneUseiInWei), amt.Mod(OneUseiInWei)
}

func (k BaseSendKeeper) SetDenomAllowList(ctx sdk.Context, denom string, allowList types.AllowList) {
	store := ctx.KVStore(k.storeKey)
	denomAllowListStore := prefix.NewStore(store, types.DenomAllowListKey(denom))

	m := k.cdc.MustMarshal(&allowList)
	denomAllowListStore.Set([]byte(denom), m)
}

func (k BaseSendKeeper) GetDenomAllowList(ctx sdk.Context, denom string) types.AllowList {
	store := ctx.KVStore(k.storeKey)
	store = prefix.NewStore(store, types.DenomAllowListKey(denom))

	bz := store.Get([]byte(denom))
	if bz == nil {
		return types.AllowList{}
	}

	var allowList types.AllowList
	k.cdc.MustUnmarshal(bz, &allowList)

	return allowList
}

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
