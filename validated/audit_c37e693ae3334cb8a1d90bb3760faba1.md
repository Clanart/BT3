### Title
Governance deposits can become permanently frozen in the module account when a depositor's address later becomes an unreceivable cast address - (File: sei-cosmos/x/gov/keeper/deposit.go)

### Summary
`AddDeposit` accepts a `MsgDeposit` (reachable directly or via the gov precompile's `deposit` method from an EVM caller) from any Sei address without checking whether that address will remain able to *receive* funds later. `RefundDeposits`, which runs during `EndBlocker` to return deposits when a proposal is finalized, explicitly skips and permanently retains any deposit whose depositor fails `CanSendTo`/`BlockedAddr` checks — mirroring the veSatin bug class where a "deposit"/"increaseAmount"-style entrypoint lacks the same guard enforced on the "withdraw" path.

### Finding Description
`AddDeposit` moves funds from the depositor into the gov module account without ever validating that the depositor address is capable of *receiving* funds back: [1](#0-0) 

The refund path, by contrast, explicitly checks `BlockedAddr`/`CanSendTo` and — if the depositor fails either check — retains the deposit forever, with a code comment stating recovery requires a migration: [2](#0-1) 

The unpayable condition is concretely reachable through the Sei↔EVM address-association mechanism: a "cast address" (`sdk.AccAddress(evmAddr[:])`) is receivable only until its EVM address is associated to a *different* true Sei address, at which point `CanSendTo`/`CanAddressReceive` starts returning `false` for that cast address: [3](#0-2) 

This is exactly the scenario exercised by the existing regression test, which deposits from a cast address, later re-associates the underlying EVM address to a different Sei address (making the original cast address permanently unreceivable), and confirms the deposit is retained forever in the module account instead of refunded: [4](#0-3) 

This is the same bug class as veSatin's `createLockForOwner`/`increaseAmount` issue: one function (`AddDeposit`, analogous to `increaseAmount`) allows funds to enter a position without validating an invariant that the counterpart function (`RefundDeposits`, analogous to `withdraw`) later enforces, causing a permanent, un-recoverable freeze of funds that back the module account balance.

### Impact Explanation
Funds deposited from a cast address that becomes unreceivable (via EVM re-association, which is a completely unprivileged, attacker-triggerable operation) are frozen permanently in the `gov` module account. There is no governance-driven or user-driven recovery path in normal operation — the code comment explicitly states recovery requires an off-chain migration. This constitutes permanent freezing of funds, matching the "Accept only concrete fund loss or permanent freezing" criterion.

### Likelihood Explanation
Reaching this state requires: (1) depositing to a governance proposal from a cast address (an address equal to `sdk.AccAddress(evmAddr[:])` for some not-yet-associated EVM address) — reachable via `MsgDeposit` or the gov precompile's `deposit()` method callable by any EVM caller — and (2) later associating that EVM address to a *different* true Sei address via the standard `addr` precompile's `associate`/`associatePubKey` flow. Both steps are ordinary, permissionless actions available to any unprivileged user. An attacker (or an unwitting user) can trigger this against their own funds, or induce a third party into this state, with no special privileges required, making this readily and repeatably triggerable.

### Recommendation
Mirror the `RefundDeposits` receivability check in `AddDeposit`: reject deposits from addresses that fail `CanSendTo`/`BlockedAddr` (or, more precisely, that are cast addresses at risk of losing receivability), just as the fix for TRST-H-3 rejected `increaseAmount` calls for veSatin tokens lacking the required invariant. Alternatively, provide an on-chain (non-migration) recovery path in `RefundDeposits` for deposits stuck against unreceivable depositors, e.g., routing them to a recoverable destination instead of leaving them permanently stuck in module-account limbo.

### Proof of Concept
1. Attacker computes an EVM address `E` for which they know the private key, and derives the cast Sei address `C = sdk.AccAddress(E[:])`, without ever associating `E` to a Sei address.
2. Attacker funds `C` (e.g., via `sendNative`/cast-address funding, cast addresses can freely receive until associated) and submits `MsgDeposit`/gov-precompile `deposit()` from `C` on an active proposal — `AddDeposit` succeeds and moves the funds into the gov module account (`sei-cosmos/x/gov/keeper/deposit.go:106-129`).
3. Before the proposal is finalized, attacker (or anyone) associates `E` to a *different* Sei address `S` via the addr precompile's `associate`/`associatePubKey`, calling `SetAddressMapping(ctx, S, E)`.
4. Now `CanAddressReceive`/`CanSendTo` returns `false` for `C` (`giga/deps/xevm/keeper/address.go:78-86`).
5. When the proposal resolves and `EndBlocker` calls `RefundDeposits`, the deposit for `C` is skipped and retained forever in the gov module account (`sei-cosmos/x/gov/keeper/deposit.go:169-192`), as directly demonstrated by `TestRefundDepositsLeavesInvalidRecipientPending` (`sei-cosmos/x/gov/keeper/deposit_test.go:180-212`).

Note: I was unable to fully trace whether the gov `deposit` precompile method itself blocks cast-address callers before reaching `AddDeposit` (only the legacy gov precompile source for `deposit` was inspected, not every code path for `MsgDeposit` validation); this should be verified in a live/full-repo session before treating the PoC as final, though the Go-level unit test above already demonstrates the underlying keeper-level freeze independent of the precompile entrypoint.

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

**File:** giga/deps/xevm/keeper/address.go (L78-86)
```go
// A sdk.AccAddress may not receive funds from bank if it's the result of direct-casting
// from an EVM address AND the originating EVM address has already been associated with
// a true (i.e. derived from the same pubkey) sdk.AccAddress.
func (k *Keeper) CanAddressReceive(ctx sdk.Context, addr sdk.AccAddress) bool {
	directCast := common.BytesToAddress(addr) // casting goes both directions since both address formats have 20 bytes
	associatedAddr, isAssociated := k.GetSeiAddress(ctx, directCast)
	// if the associated address is the cast address itself, allow the address to receive (e.g. EVM contract addresses)
	return associatedAddr.Equals(addr) || !isAssociated // this means it's either a cast address that's not associated yet, or not a cast address at all.
}
```

**File:** sei-cosmos/x/gov/keeper/deposit_test.go (L180-212)
```go
func TestRefundDepositsLeavesInvalidRecipientPending(t *testing.T) {
	app := seiapp.Setup(t, false, false, false)
	ctx := app.BaseApp.NewContext(false, tmproto.Header{})
	validDepositor := seiapp.AddTestAddrsIncremental(app, ctx, 1, sdk.NewInt(100))[0]

	proposal, err := app.GovKeeper.SubmitProposal(ctx, TestProposal)
	require.NoError(t, err)

	evmAddr := common.HexToAddress("0x0000000000000000000000000000000000000001")
	castDepositor := sdk.AccAddress(evmAddr[:])
	app.EvmKeeper.SetAddressMapping(ctx, castDepositor, evmAddr)
	require.NoError(t, app.BankKeeper.AddCoins(ctx, castDepositor, sdk.NewCoins(sdk.NewInt64Coin(sdk.DefaultBondDenom, 100)), true))

	castDeposit := sdk.NewCoins(sdk.NewInt64Coin(sdk.DefaultBondDenom, 40))
	validDeposit := sdk.NewCoins(sdk.NewInt64Coin(sdk.DefaultBondDenom, 30))
	_, err = app.GovKeeper.AddDeposit(ctx, proposal.ProposalId, castDepositor, castDeposit)
	require.NoError(t, err)
	_, err = app.GovKeeper.AddDeposit(ctx, proposal.ProposalId, validDepositor, validDeposit)
	require.NoError(t, err)

	app.EvmKeeper.SetAddressMapping(ctx, sdk.AccAddress(bytes.Repeat([]byte{2}, common.AddressLength)), evmAddr)
	require.False(t, app.BankKeeper.CanSendTo(ctx, castDepositor))

	app.GovKeeper.RefundDeposits(ctx, proposal.ProposalId)

	deposit, found := app.GovKeeper.GetDeposit(ctx, proposal.ProposalId, castDepositor)
	require.True(t, found)
	require.Equal(t, castDeposit, deposit.Amount)
	_, found = app.GovKeeper.GetDeposit(ctx, proposal.ProposalId, validDepositor)
	require.False(t, found)
	require.Equal(t, sdk.NewInt(100), app.BankKeeper.GetBalance(ctx, validDepositor, sdk.DefaultBondDenom).Amount)
	require.Equal(t, castDeposit, app.BankKeeper.GetAllBalances(ctx, app.AccountKeeper.GetModuleAddress(types.ModuleName)))
}
```
