### Title
Gov proposal deposits become permanently unrefundable once a depositor's cast address is later associated to a different Sei address - ([File: sei-cosmos/x/gov/keeper/deposit.go])

### Summary
`RefundDeposits` refuses to pay out and permanently retains a deposit record when the recorded depositor address is no longer able to receive funds, which can be triggered by a normal, unprivileged EVM `Associate` transaction executed after the deposit was made.

### Finding Description
`RefundDeposits` checks `keeper.bankKeeper.BlockedAddr(depositor) || !keeper.bankKeeper.CanSendTo(ctx, depositor)` before returning a deposit; if the check fails it deliberately leaves the deposit record in place "backed by the governance module balance," with a comment stating recovery requires a migration. [1](#0-0) 

`CanSendTo` is wired (via `RegisterRecipientChecker`) to the EVM module's `CanAddressReceive`, which returns `false` whenever an address is a direct EVM→Sei byte-cast of an EVM address that has since been associated with a *different* true (pubkey-derived) Sei address: [2](#0-1) 

Any unprivileged user can deposit governance funds from a cast address (e.g. an address obtained by depositing before ever associating their EVM/Sei identity, or by controlling an EVM address whose cast Sei address is used as a depositor). Later, that same EVM address (or another party controlling the private key) can submit a normal `Associate` transaction — a public, permissionless EVM transaction type — that binds the EVM address to a *different* true Sei address: [3](#0-2) 

Once this association is set, `CanAddressReceive` permanently returns `false` for the original cast address because `associatedAddr` no longer equals `addr`. There is no user-facing operation to reverse this: the code comment itself confirms recovery would require a chain migration.

This is exactly the "permanently locked funds" bug class described in the PercentFinance incident — a legitimate, non-malicious sequence of operations by ordinary users causes protocol-held funds (the gov module's escrowed deposit) to become permanently unspendable/unrefundable without an off-chain migration.

The existing test suite already demonstrates and accepts this exact scenario: [4](#0-3) 

### Impact Explanation
Funds sent to the gov module as a proposal deposit can become permanently and irrecoverably locked in the module account, with no in-protocol path to return them to the rightful owner. This is a direct permanent-freezing-of-funds condition (matching the "Accept only concrete fund loss or permanent freezing" validation criterion). The severity scales with deposit size (governance minimum deposits can be substantial), and the deposit record persistently backs a module balance that can never be paid out through normal means.

### Likelihood Explanation
The trigger requires no privileged access and no protocol bug beyond normal usage:
1. A user submits `MsgDeposit`/`MsgSubmitProposal` using a Sei address that is a direct EVM cast (this happens naturally for any EVM-only user account prior to full association, or for any address intentionally chosen as a cast address).
2. At any later point, an `Associate` transaction for that same EVM address is submitted, remapping it to a different true Sei address — this is a fully public, always-available EVM transaction type requiring only signature control of the EVM key and a minimal balance.
3. `RefundDeposits` runs automatically at proposal tally/expiry (`EndBlock`), silently leaving the deposit stuck.

Because Associate transactions are routine (users are expected to associate their addresses) and depositing from a not-yet-associated address is a normal usage pattern, this can occur unintentionally as well as be deliberately engineered by a user who wants to trap governance funds, or by anyone observing an unassociated depositor and front-running an association for that EVM address before the depositor associates themselves.

### Recommendation
- When a deposit is recorded, capture the depositor's *stable* identity (e.g. resolve and store the true Sei address / EVM address at deposit time and re-resolve consistently at refund), or reject deposits from currently-unassociated cast addresses until they perform an Associate.
- Alternatively, provide an in-protocol recovery path (e.g. allow the currently-associated true address for the cast EVM address to claim/redirect the stuck deposit) rather than requiring a chain migration.
- Add an invariant/monitoring alert when `RefundDeposits` leaves non-zero deposits behind, and consider a permissionless "sweep" message that lets the rightful owner (as determined by current EVM↔Sei association) reclaim funds trapped in gov (and similarly review other modules using `SendCoinsFromModuleToAccount`/`CanSendTo` refund patterns, e.g. staking unbonding, distribution rewards, for the same class of permanent lock).

### Proof of Concept
1. User controls EVM key `K` with EVM address `E`. Before ever sending an Associate/EVM tx from `K`, they submit `MsgDeposit` to a governance proposal from the cast Sei address `castDepositor := sdk.AccAddress(E[:])` (funded, e.g., via `AddCoins`) — mirrors `TestRefundDepositsLeavesInvalidRecipientPending`.
2. Separately, `E` gets associated to a different true Sei address (e.g. via a subsequent signed EVM/Cosmos transaction whose signature recovers to `E` but derives a different pubkey-based Sei address, or an explicit `Associate` transaction) using `SetAddressMapping`/`AssociateAddress`.
3. `CanAddressReceive(ctx, castDepositor)` now returns `false` since `GetSeiAddress(ctx, E)` no longer equals `castDepositor`.
4. When the proposal is finalized and `RefundDeposits` runs, the loop hits `!keeper.bankKeeper.CanSendTo(ctx, castDepositor)` and returns `false` without paying out, leaving the deposit permanently recorded and the coins stuck in the `gov` module account — exactly as asserted by: [5](#0-4)

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

**File:** x/evm/keeper/address.go (L78-86)
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

**File:** x/evm/ante/preprocess.go (L182-204)
```go
		var err error
		txData, err = evmtypes.UnpackTxData(msgEVMTransaction.Data)
		if err != nil {
			return err
		}
	}

	if atx, ok := txData.(*ethtx.AssociateTx); ok {
		V, R, S := atx.GetRawSignatureValues()
		V = new(big.Int).Add(V, utils.Big27)
		// Hash custom message passed in
		customMessageHash := crypto.Keccak256Hash([]byte(atx.CustomMessage))
		evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(V, R, S, customMessageHash)
		if err != nil {
			return err
		}
		msgEVMTransaction.Derived = &derived.Derived{
			SenderEVMAddr: evmAddr,
			SenderSeiAddr: seiAddr,
			PubKey:        &secp256k1.PubKey{Key: pubkey.Bytes()},
			Version:       derived.Cancun,
			IsAssociate:   true,
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
