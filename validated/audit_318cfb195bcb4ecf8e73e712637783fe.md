### Title
Address re-association can render a delegator's `CanSendTo` check permanently false, freezing unbonded stake / halting chain via `UndelegateCoins` — analogous to blocklist-driven fund lockup - ([File: sei-cosmos/x/bank/keeper/keeper.go])

### Summary
The Sei bank module gates every credit to an address behind `BaseSendKeeper.CanSendTo`, which in turn checks `EvmKeeper.CanAddressReceive` — a predicate whose truth value depends on the *mutable* EVM↔Sei address-association mapping rather than on any user-controlled, reversible flag. [1](#0-0)  A direct-cast address (`sdk.AccAddress(evmAddr[:])`) that has already accumulated on-chain obligations (e.g. a staking delegation) can be turned into a permanently "blocked recipient" the moment its true owner associates their EVM address with a *different* Sei address, exactly mirroring the FIAT DAO bug where changing the blocklist value can trap an account that was never previously blocked. [2](#0-1) 

### Finding Description
`UndelegateCoins`, which the staking module calls to pay out matured unbonding delegations, explicitly rejects sends to a delegator address that fails `CanSendTo`: [3](#0-2) 

`CanSendTo`/`AddCoins` in turn gate every incoming credit on `EvmKeeper.CanAddressReceive`, which returns `false` for a direct-cast address once its underlying EVM address becomes associated with a *different* true Sei address: [1](#0-0) 

This is a state transition the *account itself* triggers by using the chain normally — e.g. delegating stake from the un-associated, direct-cast Sei address, then later signing any Cosmos or EVM transaction, which the ante-handler auto-associates to a different, pubkey-derived Sei address: [4](#0-3) 

Once that happens, `CanSendTo(castAddr)` is permanently `false` (verified by the existing regression test suite), and any code path that must pay the delegator back at the cast address will error out with `sdkerrors.ErrInvalidRecipient`: [5](#0-4) 

Unlike the `x/distribution` module — which was hardened specifically for this scenario (falling back to the operator address or routing unpayable commission to the community pool so `AfterValidatorRemoved` never panics during `EndBlock`) — the plain `UndelegateCoins` path in `x/bank` has no such fallback; it simply returns an error to whatever caller invoked it (the staking module's unbonding-completion logic in `EndBlocker`). There is no "unblock" mechanism analogous to what the original report recommends: the direct-cast address can never regain the ability to receive funds because re-associating the same Sei address again is explicitly rejected ("address already has association set"). [6](#0-5) 

### Impact Explanation
If the unbonding-completion logic in the staking module's `EndBlocker` calls `UndelegateCoins`/`CompleteUnbonding` and does not itself special-case an `ErrInvalidRecipient` result, one of two things happens per the analog's bug class:
- The unbonded tokens remain stuck in the `NotBondedPool` module account indefinitely, with no way for the affected delegator to retrieve them (permanent freezing of funds), or
- If the error is not swallowed, it propagates up through `EndBlocker` and panics, halting the chain (validator halt).

Both outcomes match the accepted impact categories (permanent freezing of funds / validator halt). The scenario is fully reachable by an ordinary user: delegate from an un-associated address, then trigger association through any subsequent signed transaction — no admin, governance, or malicious-peer action is required.

### Likelihood Explanation
Medium. It requires a specific but realistic sequence: a user must delegate stake (or otherwise become owed a bank credit) using their un-associated, direct-cast address before ever associating their EVM key to a different Sei address, and later sign a transaction that triggers association. Sei's own test suite demonstrates this exact address divergence occurring for validator operator addresses, confirming the precondition is a known, real occurrence rather than a purely theoretical one. [7](#0-6) 

### Recommendation
Apply the same defensive pattern already used in `x/distribution`'s `Hooks.AfterValidatorRemoved`/`SetWithdrawAddr` to every other bank-credit path that can target a potentially-unreceivable address, in particular `UndelegateCoins`/staking's unbonding completion: check `CanSendTo`/`CanAddressReceive` up front and, if the target cannot receive, fail closed by refusing to complete the state transition (or route to a recoverable module account) instead of losing the payout, and never let an `EndBlocker` operation panic on this condition. Consider giving users a supported "reclaim" path to migrate residual value from their direct-cast address after association, analogous to the report's recommended "unblock" functionality.

### Proof of Concept
1. A brand-new key generates EVM address `E` and delegates stake to a validator using the direct-cast Sei address `castAddr = sdk.AccAddress(E[:])` (no association has occurred yet, so `CanSendTo(castAddr)` is `true`).
2. The same private key later signs any Cosmos or EVM transaction; the EVM ante `AssociateAddresses` step associates `E` with the *pubkey-derived* "true" Sei address `trueAddr != castAddr`, exactly as shown in the existing test `TestAfterValidatorRemovedRoutesToCommunityPoolForUnreceivableValidator`. [5](#0-4) 
3. From this point, `app.BankKeeper.CanSendTo(ctx, castAddr)` returns `false` permanently, and any subsequent unbonding completion for the delegation created in step 1 that attempts `UndelegateCoins(..., castAddr, ...)` will hit `sdkerrors.ErrInvalidRecipient` in `sei-cosmos/x/bank/keeper/keeper.go` lines 264–277, with no supported way for the user to make `castAddr` receivable again.

**Uncertainty / what I could not fully verify:** I was not able to locate and inspect the exact staking-module call site that invokes `UndelegateCoins` during unbonding completion (e.g. `CompleteUnbonding` in `x/staking`/`sei-cosmos/x/staking/keeper`) within the indexed context, so I cannot confirm with certainty whether that call site (a) panics on error and halts the chain, or (b) silently leaves funds stuck in the not-bonded pool, or (c) already contains a distribution-style fallback that I did not find. This would need to be confirmed by reading `sei-cosmos/x/staking/keeper/delegation.go` (`CompleteUnbonding`) directly, which the index did not surface in these searches — I recommend starting a full Devin session to inspect that file if precise confirmation of the exact failure mode (freeze vs. panic) is required.

### Citations

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

**File:** x/evm/ante/preprocess.go (L74-101)
```go
	isAssociateTx := derived.IsAssociate
	associateHelper := helpers.NewAssociationHelper(p.evmKeeper, p.evmKeeper.BankKeeper(), p.accountKeeper)
	_, isAssociated := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if isAssociateTx && isAssociated {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "account already has association set")
	} else if isAssociateTx {
		// check if the account has enough balance (without charging)
		if !p.IsAccountBalancePositive(ctx, seiAddr, evmAddr) {
			assocErr := evmtypes.NewAssociationMissingErr(seiAddr.String())
			evmAnteMetrics.associationError.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("scenario", "associate_tx_insufficient_funds"), attribute.String("type", assocErr.AddressType())))
			return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, "account needs to have at least 1 wei to force association")
		}
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}

		return ctx.WithPriority(antedecorators.EVMAssociatePriority), nil // short-circuit without calling next
	} else if isAssociated {
		// noop; for readability
	} else {
		// not associatedTx and not already associated
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}
		if p.evmKeeper.EthReplayConfig.Enabled {
			p.evmKeeper.PrepareReplayedAddr(ctx, evmAddr)
		}
	}
```

**File:** sei-cosmos/x/bank/keeper/keeper.go (L264-277)
```go
func (k BaseKeeper) UndelegateCoins(ctx sdk.Context, moduleAccAddr, delegatorAddr sdk.AccAddress, amt sdk.Coins) error {
	moduleAcc := k.ak.GetAccount(ctx, moduleAccAddr)
	if moduleAcc == nil {
		return sdkerrors.Wrapf(sdkerrors.ErrUnknownAddress, "module account %s does not exist", moduleAccAddr)
	}

	if !amt.IsValid() {
		return sdkerrors.Wrap(sdkerrors.ErrInvalidCoins, amt.String())
	}
	if !k.CanSendTo(ctx, delegatorAddr) {
		return sdkerrors.ErrInvalidRecipient
	}

	err := k.SubUnlockedCoins(ctx, moduleAccAddr, amt, true)
```

**File:** sei-cosmos/x/distribution/keeper/keeper_test.go (L95-102)
```go
// TestAfterValidatorRemovedRoutesToCommunityPoolForUnreceivableValidator covers the
// case where the validator operator address itself cannot receive funds — its EVM
// address was re-associated (e.g. via associatePubKey) away from the direct-cast Sei
// address it was created under. The withdraw-address fallback in GetDelegatorWithdrawAddr
// resolves back to that same unreceivable operator address, so the commission
// force-withdraw fails. AfterValidatorRemoved runs during EndBlock, so it must not panic:
// the commission is routed to the community pool instead, which conserves value because
// the coins already back the distribution module account.
```

**File:** sei-cosmos/x/distribution/keeper/keeper_test.go (L103-123)
```go
func TestAfterValidatorRemovedRoutesToCommunityPoolForUnreceivableValidator(t *testing.T) {
	app := seiapp.Setup(t, false, false, false)
	ctx := app.BaseApp.NewContext(false, tmproto.Header{})

	// The validator operator is the direct-cast Sei address of an EVM address.
	evmAddr := common.HexToAddress("0x3333333333333333333333333333333333333333")
	castAddr := sdk.AccAddress(evmAddr[:])
	valAddr := sdk.ValAddress(castAddr)
	valAccAddr := sdk.AccAddress(valAddr) // == castAddr

	require.True(t, app.BankKeeper.CanSendTo(ctx, castAddr))

	// Re-associate the EVM address to a different true Sei address, mirroring
	// associatePubKey after a validator was created under the direct-cast address.
	associatedAddr := seiapp.AddTestAddrs(app, ctx, 1, sdk.NewInt(1000000000))[0]
	app.EvmKeeper.SetAddressMapping(ctx, associatedAddr, evmAddr)

	// The operator/delegator address can no longer receive funds, and the
	// withdraw-address fallback resolves back to that same unreceivable address.
	require.False(t, app.BankKeeper.CanSendTo(ctx, castAddr))
	require.Equal(t, valAccAddr.String(), app.DistrKeeper.GetDelegatorWithdrawAddr(ctx, valAccAddr).String())
```
