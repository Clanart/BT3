Based on my research, I found a genuine analog in sei-chain's EVM address-association mechanism, where `SelfDestruct` resets a "gate" state to its default/unassociated value, which the bank keeper's `CanAddressReceive`/`CanSendTo` check treats as "safe to receive again" — mirroring the BullvBear pattern of resetting a match-guard to `address(0)` to re-enable an action that should only ever happen once per identity.

### Title
EVM `SelfDestruct` deletes the EVM↔Sei address association, resetting `CanAddressReceive`'s "already associated" guard to its default (unassociated) state and re-exposing the stale direct-cast address to bank transfers - (File: `x/evm/state/state.go`, `x/evm/keeper/address.go`)

### Summary
`CanAddressReceive` in `x/evm/keeper/address.go` gates whether a direct-cast Sei address (`sdk.AccAddress(evmAddress[:])`) may still receive bank funds, by checking whether that EVM address has since been associated with a "true" (pubkey-derived) Sei address. Once the mapping is deleted — which `SelfDestruct` does unconditionally for any address that currently has a stored association — the guard resets to its default "not associated" state, `!isAssociated == true`, and the stale cast address becomes receivable again, exactly like the BullvBear pattern where `bulls[uint(orderHash)] == address(0)` was treated as "not yet matched."

### Finding Description
`CanAddressReceive` is documented as a one-way guard: [1](#0-0)  Once a "true" association exists for an EVM address, its direct-cast Sei address must no longer be a valid bank recipient, because balances sent to the cast address would be inaccessible to the real owner (they can only spend through the associated true Sei address).

This gate is implemented purely as a KV-store lookup keyed by the association mapping: [2](#0-1) . Both `SetAddressMapping` and `DeleteAddressMapping` are freely callable by keeper logic that runs as part of normal (unprivileged) transaction execution — specifically, `DBImpl.SelfDestruct` calls `DeleteAddressMapping` unconditionally whenever the destructing address currently has an association: [3](#0-2) .

Because `SELFDESTRUCT` is triggerable by any EVM contract via ordinary contract logic in a single submitted transaction, any account holder whose EVM address is associated (whether an EOA that later deployed self-destructing contract code at that address, or specifically via EIP-7702 `SetCode` delegation installing self-destructing code at an EOA's address) can delete their own EVM↔Sei mapping mid-transaction. Immediately after, `CanAddressReceive`/`CanSendTo` treat the direct-cast address as if it had never been associated, satisfying `!isAssociated`, and the bank module will again permit balances to accumulate at the direct-cast address: [4](#0-3) . Tests confirm this exact toggle behavior — funds are blocked once associated, then unblocked once the mapping is torn down: [5](#0-4)  and unit tests explicitly verifying re-association changes `CanSendTo`/withdraw-address resolution: [6](#0-5) .

This is the same bug class as H-2: a "used/committed" state is represented only by presence vs. absence of a mapping entry, and the entity that would benefit from resetting that state (the EVM-address owner via `SelfDestruct`, or an attacker forcing an association away via `associatePubKey`/EIP-7702) can trigger the deletion/overwrite themselves, re-enabling logic (`CanAddressReceive` in this case, "already matched" in BullvBear) that was only meant to fire once.

The codebase's own inline comments confirm the maintainers are aware re-association is a live hazard for downstream Cosmos modules (staking/distribution), and have partially mitigated it only for the EIP-7702 `SetCode` path: [7](#0-6) . However, `SelfDestruct`'s unconditional `DeleteAddressMapping` (not just `SetCode`/`associatePubKey` re-association) is not covered by that mitigation, and directly undoes the `CanAddressReceive` guard for any previously-associated address.

### Impact Explanation
Once an EVM address that has already migrated funds/identity to a "true" associated Sei address self-destructs its deployed code (or is force-associated via a subsequent path that overwrites/removes the mapping), the stale direct-cast Sei address becomes a valid bank recipient again. Funds subsequently routed there by unaware third parties (e.g. by distribution validator-removal fallback withdraw logic, or by anyone paying the raw EVM-derived cast address) become **stuck/inaccessible to the intended owner**, because the real owner can only spend via keys tied to the true associated Sei address, not the direct-cast address — this is precisely the failure mode the guard exists to prevent, per its own doc comment. This matches the required impact class of concrete fund loss/freezing via an unprivileged, single-transaction-reachable EVM path (`SELFDESTRUCT`).

### Likelihood Explanation
`SELFDESTRUCT` is trivially reachable by deploying a small contract at an already-associated EOA/contract address (or via EIP-7702 delegation) and calling it — a single unprivileged transaction. No special permissions, validator collusion, or network conditions are required; the mapping deletion happens deterministically as part of core EVM state-transition logic, and is directly exercised/observed in the repo's own tests.

### Recommendation
Do not treat "no stored mapping" as equivalent to "never associated." Either (a) persist a permanent tombstone/"was-associated" flag for direct-cast addresses that is never cleared by `SelfDestruct`/re-association, and have `CanAddressReceive` consult that tombstone instead of raw mapping presence, or (b) make `SelfDestruct` not clear the `SeiAddressToEVMAddressKey`/`EVMAddressToSeiAddressKey` entries used by `CanAddressReceive`, keeping a separate, append-only history of associations for the purposes of this guard.

### Proof of Concept
1. Associate EVM address `E` with true Sei address `S` via a signed `associate`/EVM tx (`SetAddressMapping(S, E)`), matching `TestSendingToCastAddress`'s setup: [5](#0-4) . At this point `CanAddressReceive(castAddr(E))` returns `false`, blocking sends to `castAddr(E)`.
2. Deploy a contract with a `SELFDESTRUCT` opcode to address `E` (or install EIP-7702 delegation code at `E` and invoke self-destruct), executed as an ordinary transaction from `E`.
3. `DBImpl.SelfDestruct` fires, calling `k.DeleteAddressMapping(ctx, S, E)`: [8](#0-7) , removing both directions of the mapping.
4. `CanAddressReceive(castAddr(E))` now evaluates `isAssociated == false`, so the guard returns `true` again — `castAddr(E)` is once more a valid bank recipient, even though `E`'s true identity `S` is still the intended controller of any funds tied to that identity, reproducing the "guard reset via self-triggered state deletion" pattern from the BullvBear report.

### Citations

**File:** x/evm/keeper/address.go (L10-28)
```go
func (k *Keeper) SetAddressMapping(ctx sdk.Context, seiAddress sdk.AccAddress, evmAddress common.Address) {
	store := ctx.KVStore(k.storeKey)
	store.Set(types.EVMAddressToSeiAddressKey(evmAddress), seiAddress)
	store.Set(types.SeiAddressToEVMAddressKey(seiAddress), evmAddress[:])
	if !k.accountKeeper.HasAccount(ctx, seiAddress) {
		k.accountKeeper.SetAccount(ctx, k.accountKeeper.NewAccountWithAddress(ctx, seiAddress))
	}
	ctx.EventManager().EmitEvent(sdk.NewEvent(
		types.EventTypeAddressAssociated,
		sdk.NewAttribute(types.AttributeKeySeiAddress, seiAddress.String()),
		sdk.NewAttribute(types.AttributeKeyEvmAddress, evmAddress.Hex()),
	))
}

func (k *Keeper) DeleteAddressMapping(ctx sdk.Context, seiAddress sdk.AccAddress, evmAddress common.Address) {
	store := ctx.KVStore(k.storeKey)
	store.Delete(types.EVMAddressToSeiAddressKey(evmAddress))
	store.Delete(types.SeiAddressToEVMAddressKey(seiAddress))
}
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

**File:** x/evm/state/state.go (L82-98)
```go
// debits account's balance. The corresponding credit happens here:
// https://github.com/sei-protocol/go-ethereum/blob/master/core/vm/instructions.go#L825
// clear account's state except the transient state (in Ethereum transient states are
// still available even after self destruction in the same tx)
func (s *DBImpl) SelfDestruct(acc common.Address) uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, acc)
	if seiAddr, ok := s.k.GetSeiAddress(s.ctx, acc); ok {
		// remove the association
		s.k.DeleteAddressMapping(s.ctx, seiAddr, acc)
	}
	b := s.GetBalance(acc)
	s.SubBalance(acc, b, tracing.BalanceDecreaseSelfdestruct)

	// mark account as self-destructed
	s.MarkAccount(acc, AccountDeleted)
	return *b
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L252-278)
```go
		}

		var newBalance sdk.Coin
		if checkNeg {
			newBalance = balance.Sub(coin)
		} else {
			newBalance = balance.SubUnsafe(coin)
		}

		err := k.setBalance(ctx, addr, newBalance, checkNeg)
		if err != nil {
			return err
		}
	}

	// emit coin spent event
	ctx.EventManager().EmitEvent(
		types.NewCoinSpentEvent(addr, amt),
	)
	return nil
}

// AddCoins increase the addr balance by the given amt. Fails if the provided amt is invalid.
// It emits a coin received event.
func (k BaseSendKeeper) AddCoins(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Coins, checkNeg bool) error {
	if !k.CanSendTo(ctx, addr) {
		return sdkerrors.ErrInvalidRecipient
```

**File:** x/evm/keeper/address_test.go (L58-75)
```go
func TestSendingToCastAddress(t *testing.T) {
	a := keeper.EVMTestApp
	ctx := a.GetContextForDeliverTx([]byte{})
	seiAddr, evmAddr := keeper.MockAddressPair()
	castAddr := sdk.AccAddress(evmAddr[:])
	sourceAddr, _ := keeper.MockAddressPair()
	require.Nil(t, a.BankKeeper.MintCoins(ctx, "evm", sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(10)))))
	require.Nil(t, a.BankKeeper.SendCoinsFromModuleToAccount(ctx, "evm", sourceAddr, sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(5)))))
	amt := sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(1)))
	require.Nil(t, a.BankKeeper.SendCoinsFromModuleToAccount(ctx, "evm", castAddr, amt))
	require.Nil(t, a.BankKeeper.SendCoins(ctx, sourceAddr, castAddr, amt))
	require.Nil(t, a.BankKeeper.SendCoinsAndWei(ctx, sourceAddr, castAddr, sdk.OneInt(), sdk.OneInt()))

	a.EvmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	require.NotNil(t, a.BankKeeper.SendCoinsFromModuleToAccount(ctx, "evm", castAddr, amt))
	require.NotNil(t, a.BankKeeper.SendCoins(ctx, sourceAddr, castAddr, amt))
	require.NotNil(t, a.BankKeeper.SendCoinsAndWei(ctx, sourceAddr, castAddr, sdk.OneInt(), sdk.OneInt()))
}
```

**File:** sei-cosmos/x/distribution/keeper/keeper_test.go (L103-148)
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

	commission := sdk.DecCoins{sdk.NewDecCoin("usei", sdk.NewInt(10))}
	coins := sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(10)))
	distrAcc := app.DistrKeeper.GetDistributionAccount(ctx)
	require.NoError(t, apptesting.FundModuleAccount(app.BankKeeper, ctx, distrAcc.GetName(), coins))
	app.AccountKeeper.SetModuleAccount(ctx, distrAcc)

	app.DistrKeeper.SetValidatorOutstandingRewards(ctx, valAddr, types.ValidatorOutstandingRewards{Rewards: commission})
	app.DistrKeeper.SetValidatorAccumulatedCommission(ctx, valAddr, types.ValidatorAccumulatedCommission{Commission: commission})

	communityBefore := app.DistrKeeper.GetFeePool(ctx).CommunityPool.AmountOf("usei")
	moduleBalanceBefore := app.BankKeeper.GetBalance(ctx, distrAcc.GetAddress(), "usei")

	require.NotPanics(t, func() {
		app.DistrKeeper.Hooks().AfterValidatorRemoved(ctx, sdk.ConsAddress{}, valAddr)
	})

	// The commission could not be paid out, so it stays in the distribution module
	// account and is accounted to the community pool. No value leaves the module and
	// the unreceivable operator address receives nothing.
	communityAfter := app.DistrKeeper.GetFeePool(ctx).CommunityPool.AmountOf("usei")
	require.Equal(t, communityBefore.Add(sdk.NewDec(10)), communityAfter)
	require.True(t, app.BankKeeper.GetBalance(ctx, castAddr, "usei").IsZero())
	require.Equal(t, moduleBalanceBefore, app.BankKeeper.GetBalance(ctx, distrAcc.GetAddress(), "usei"))
}
```

**File:** app/ante/evm_checktx.go (L251-284)
```go
// AssociateAuthorizationAuthorities pre-associates every EIP-7702 SetCode authorization
// authority in the transaction with its true (pubkey-derived) Sei address before EVM
// execution installs delegation code for it. Authorities are distinct accounts from the tx
// sender, so the sender association performed by the caller does not cover them. Without
// this, SetCode creates a mutable direct-cast EVM->Sei mapping that a later associatePubKey
// call can remap, orphaning any staking/distribution state created under the direct-cast
// identity (which can then halt the chain via the distribution validator-removal hook).
//
// This is the legacyabci counterpart of x/evm/ante's EVMPreprocessDecorator so both ante
// paths associate authorities identically. It is best-effort: only authorities whose
// authorization the EVM will actually apply are pre-associated — helpers.AuthorityToPreAssociate
// enforces the same chain-id/nonce/account-code checks go-ethereum uses, which also prevents
// replaying a publicly-visible authorization a user signed for another chain to force-associate
// them. Each association runs in its own cache context and is only committed on success, so an
// already-associated, skipped, or failing authority affects only itself and never rejects the
// transaction (matching go-ethereum, which would still accept it). Non-SetCode transactions
// carry no authorizations and are a no-op.
func AssociateAuthorizationAuthorities(ctx sdk.Context, ek *evmkeeper.Keeper, etx *ethtypes.Transaction) {
	auths := etx.SetCodeAuthorizations()
	if len(auths) == 0 {
		return
	}
	associateHelper := helpers.NewAssociationHelper(ek, ek.BankKeeper(), ek.AccountKeeper())
	for _, auth := range auths {
		evmAddr, seiAddr, pubkey, ok := helpers.AuthorityToPreAssociate(ctx, ek, auth)
		if !ok {
			continue
		}
		cacheCtx, write := ctx.CacheContext()
		if err := associateHelper.AssociateAddresses(cacheCtx, seiAddr, evmAddr, pubkey, false); err == nil {
			write()
		}
	}
}
```
