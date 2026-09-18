# Analysis: Permanent freeze of governance deposits for EVM-associated depositors

## Title
Governance deposits from EVM-cast addresses become permanently locked in the module account when the address association changes - (File: `sei-cosmos/x/gov/keeper/deposit.go`)

## Summary
The Celo report describes funds becoming permanently locked in an escrow-like contract when the completion condition can no longer be satisfied, and states this is accepted-by-design. Sei's `x/gov` deposit-refund path has a structurally similar "funds become permanently stuck" outcome, but it is not a deliberate anti-spam mechanism — it is a documented gap requiring a manual migration to ever recover the funds, reachable purely through ordinary user transactions (governance deposit + EVM address association).

## Finding Description
`RefundDeposits` refunds a proposal's deposits back to each depositor, but explicitly skips any depositor for whom `bankKeeper.BlockedAddr` or `!bankKeeper.CanSendTo` is true, leaving the deposit record (and the backing coins in the `gov` module account) in place forever: [1](#0-0) 

`CanSendTo` delegates to a set of pluggable `RecipientChecker`s registered on the bank keeper: [2](#0-1) 

The existing unit test `TestRefundDepositsLeavesInvalidRecipientPending` demonstrates the concrete attack surface: a depositor address derived from an EVM address (`sdk.AccAddress(evmAddr[:])`) deposits on a proposal, and the account owner subsequently re-associates that EVM address to a different Sei address via `EvmKeeper.SetAddressMapping`. After this, `CanSendTo` returns `false` for the original depositor address, and `RefundDeposits` leaves that deposit permanently pending in the module account instead of ever returning it: [3](#0-2) 

Unlike the Celo case — where forfeiture is an intentional anti-spam cost — nothing in this code path indicates the lock is a deliberate design choice; the comment says recovery "requires a migration," which is an operational escape hatch rather than an intended protocol invariant.

## Impact Explanation
Any depositor whose account is a cast EVM address (reachable by any ordinary user through the standard `x/evm` association flow) can have their governance deposit permanently frozen in the `gov` module account by re-associating (or by simply having a different address re-associated to) the underlying EVM address before/when the proposal deposit is refunded. This is a genuine permanent freezing of funds with no path to recovery short of a chain upgrade/migration, matching the "permanent freezing of funds" acceptance criterion.

## Likelihood Explanation
The freeze requires the depositor's `CanSendTo` check to fail at refund time, which in the demonstrated case happens purely from a self-initiated EVM re-association — a routine, unprivileged action any EVM user can perform. It does not require a malicious counterparty, validator, or governance action; it can occur incidentally (a user re-associates their EVM address for legitimate reasons while a deposit is outstanding) or be self-inflicted deliberately. Because it only freezes the depositor's own funds (deposits are always keyed to the sender), it is not directly usable to steal funds from a third party, which somewhat limits severity relative to a true fund-theft bug, but it still constitutes a real, permanent loss of the depositor's own funds with no code-level recovery path.

## Recommendation
When `RefundDeposits` cannot deliver funds to a depositor because `CanSendTo`/`BlockedAddr` fails, provide either: (a) an explicit governance-triggered recovery/claim mechanism keyed by proposal+depositor rather than relying on an ad-hoc migration, or (b) resolve the refund against the depositor's *current* address mapping (e.g., re-resolve the EVM-associated Sei address at refund time) so that address re-association does not orphan the deposit.

## Proof of Concept
The existing test already reproduces the freeze end-to-end: [3](#0-2) 
1. `validDepositor` and `castDepositor` (an `sdk.AccAddress` derived from EVM address `evmAddr`) both deposit on a proposal.
2. The EVM address mapping for `evmAddr` is changed to point to a different account (`EvmKeeper.SetAddressMapping(ctx, sdk.AccAddress(bytes.Repeat([]byte{2}, common.AddressLength)), evmAddr)`), making `CanSendTo(castDepositor)` return `false`.
3. `RefundDeposits` is called; `validDepositor`'s deposit is refunded and deleted, but `castDepositor`'s deposit record and coins remain permanently in the `gov` module account, with no code path to reclaim them.

### Citations

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

**File:** sei-cosmos/x/bank/keeper/send.go (L465-472)
```go
func (k BaseSendKeeper) CanSendTo(ctx sdk.Context, recipient sdk.AccAddress) bool {
	for _, rc := range *k.recipientCheckers {
		if !rc(ctx, recipient) {
			return false
		}
	}
	return true
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
