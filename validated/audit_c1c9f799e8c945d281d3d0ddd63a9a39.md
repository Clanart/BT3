This confirms the analog. The `associatePublicKey` precompile function is callable by **any unprivileged EVM caller** with only a public key (no private-key proof, no signature required), and it unconditionally remaps the EVM↔Sei address pairing via `SetAddressMapping`, which overwrites both the forward and reverse KV entries.### Title
Unauthenticated `associatePubKey` precompile lets any caller force-remap a victim's EVM↔Sei identity, orphaning validator/delegation state - (File: precompiles/addr/addr.go)

### Summary
The `addr` precompile's `associatePubKey` method sets the global EVM↔Sei address mapping for a target account using nothing but that account's already-derivable **public key** — no signature, no proof of private-key ownership, and no consent from the affected account is required. Any unprivileged EVM caller can invoke it for any victim whose public key is publicly known (e.g., visible from any prior transaction they signed, exactly the kind of "publicly-visible" credential the code's own EIP-7702 comments warn about). This mirrors the HomeFi finding's core theme: a supposedly privileged/self-serve identity or admin-binding action can be triggered by someone other than the rightful controller, causing loss of control over on-chain state tied to that identity.

### Finding Description
`PrecompileExecutor.associatePublicKey` in `precompiles/addr/addr.go` (lines 205–237) accepts a raw compressed public key hex string as its only argument, parses it, derives `evmAddr`/`seiAddr` from it, and passes them straight to `associateAddresses`: [1](#0-0) 

`associateAddresses` only checks that the **target `seiAddr`** is not already associated — it performs no check on `msg.sender`/`caller` matching the derived address, and no signature verification ties the caller to the supplied public key: [2](#0-1) 

Once accepted, `AssociateAddresses` → `SetAddressMapping` unconditionally overwrites both the forward and reverse KV mapping entries for the derived `evmAddr`/`seiAddr` pair: [3](#0-2) 

The codebase's own comments acknowledge this exact remap hazard for the related EIP-7702 flow: a mutable direct-cast EVM→Sei mapping "can [be remapped by] a later `associatePubKey` call... orphaning any staking/distribution state created under the direct-cast identity": [4](#0-3) 

The mitigations that exist (`AfterValidatorRemoved` routing to community pool, `GetDelegatorWithdrawAddr` fallback, `SetWithdrawAddr` blocklist checks) only prevent an EndBlock **panic**; they do not prevent the underlying unauthorized remap itself: [5](#0-4) [6](#0-5) 

Because `associatePubKey` requires no signature at all (unlike `associate`, which requires an ECDSA signature over a custom message to prove key ownership, at `precompiles/addr/addr.go` lines 160-203), any address whose secp256k1 public key has ever been exposed on-chain (which is virtually every active account, since public keys are revealed the first time an account signs a transaction) can have its EVM↔Sei mapping forcibly created/changed by an unrelated third party.

### Impact Explanation
An attacker can call `associatePubKey` for a victim's cast (direct-cast) address that already has accrued state — e.g., a validator operator address, a delegator, or an account holding funds under its cast identity — to remap the EVM address away from that cast Sei address. This:
- Makes the previous cast Sei address permanently unable to receive future funds (`CanAddressReceive`/`CanSendTo` return false for it), per the module's own dual-address design and tests. [7](#0-6) 
- Diverts validator commission/rewards intended for that operator to the community pool instead of the rightful validator, as shown by the existing regression test that models exactly this remap-by-`associatePubKey` scenario: [8](#0-7) 
- Can be triggered against any staking/distribution/bank state accrued under a cast address, without the victim's consent, at the attacker's chosen time — a form of unauthorized state takeover/fund misdirection analogous to the HomeFi report's "impersonate a privileged operation without the real owner's consent."

This is a fund-misdirection and permanent-freezing class issue (rewards/commission are diverted from the rightful party and the cast address becomes permanently unreceivable), which meets the Medium+ bar for concrete fund loss/freezing.

### Likelihood Explanation
Likelihood is non-trivial: the only prerequisite is knowledge of the victim's secp256k1 public key, which is exposed the moment that account signs any Cosmos or EVM transaction (it is embedded in the signature and readily extractable, or simply queryable via `GetSeiAddress`/`GetEVMAddress` for already-associated accounts). No special privilege, private key, or victim cooperation is needed — any public-RPC client can submit the precompile call. The most valuable targets (validators, active delegators with unassociated cast addresses) are also the ones most likely to have exposed their pubkey through normal chain activity.

### Recommendation
- Require `associatePublicKey` to only associate the mapping for `caller` (`msg.sender`)'s own derived address, or require a signature (as `associate`/`associatePublicKeyWithSignature` do) proving the caller controls the private key corresponding to the supplied public key.
- Alternatively, block remapping of any Sei address that already has accrued staking/distribution/bank state under its cast identity (i.e., disallow `associatePubKey` from silently reassigning an `evmAddr` whose direct-cast Sei address already has an existing validator, active delegations, or non-zero balance) unless the true controller performs the association themselves via a signed operation.
- Audit all other precompile/keeper entry points that call `AssociateAddresses`/`SetAddressMapping` for the same missing-authentication pattern.

### Proof of Concept
1. Victim `V` signs any transaction on Sei (Cosmos or EVM), exposing their secp256k1 public key on-chain.
2. `V`'s EVM address is currently mapped via direct-cast to `castAddr = bytes(evmAddr)`, and `V` has created a validator or accrued rewards under `castAddr` (this is the default/most common state for any user who has not yet explicitly associated their true pubkey-derived address).
3. Attacker `A` extracts `V`'s public key from step 1 and calls the `addr` precompile's `associatePubKey(pubKeyHex)` from their own EVM account — no signature over any message is required.
4. `associatePublicKey` derives `evmAddr`/`trueSeiAddr` from the supplied pubkey and calls `associateAddresses`, which succeeds because only `trueSeiAddr` (not yet associated) is checked, not `castAddr`.
5. `SetAddressMapping` now points `evmAddr` to `trueSeiAddr` instead of `castAddr`. `castAddr` becomes unreceivable (`CanAddressReceive` false), matching the existing regression test: [8](#0-7) 
6. Any commission/rewards subsequently due to the validator under `castAddr` are diverted to the community pool instead of reaching `V`, and `V` cannot recover control of state accrued under `castAddr` without an explicit remediation path, since the attacker (not the victim) triggered the remap.

### Citations

**File:** precompiles/addr/addr.go (L205-237)
```go
func (p PrecompileExecutor) associatePublicKey(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}

	// Takes a single argument, a compressed pubkey in hex format, excluding the '0x'
	pubKeyHex := args[0].(string)

	pubKeyBytes, err := hex.DecodeString(pubKeyHex)
	if err != nil {
		return nil, 0, err
	}

	// Parse the compressed public key
	pubKey, err := btcec.ParsePubKey(pubKeyBytes)
	if err != nil {
		return nil, 0, err
	}

	// Convert to uncompressed public key
	uncompressedPubKey := pubKey.SerializeUncompressed()

	evmAddr, seiAddr, pubkey, err := helpers.GetAddressesFromPubkeyBytes(uncompressedPubKey)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
}
```

**File:** precompiles/addr/addr.go (L239-255)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}

	// Associate Addresses:
	associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.bankKeeper, p.accountKeeper)
	err = associationHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false)
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(seiAddr.String(), evmAddr)
	return ret, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** x/evm/keeper/address.go (L10-22)
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

**File:** app/ante/evm_checktx.go (L251-257)
```go
// AssociateAuthorizationAuthorities pre-associates every EIP-7702 SetCode authorization
// authority in the transaction with its true (pubkey-derived) Sei address before EVM
// execution installs delegation code for it. Authorities are distinct accounts from the tx
// sender, so the sender association performed by the caller does not cover them. Without
// this, SetCode creates a mutable direct-cast EVM->Sei mapping that a later associatePubKey
// call can remap, orphaning any staking/distribution state created under the direct-cast
// identity (which can then halt the chain via the distribution validator-removal hook).
```

**File:** sei-cosmos/x/distribution/keeper/hooks.go (L44-72)
```go
		// add to validator account
		if !coins.IsZero() {
			accAddr := sdk.AccAddress(valAddr)
			withdrawAddr := h.k.GetDelegatorWithdrawAddr(ctx, accAddr)

			// GetDelegatorWithdrawAddr falls back to the delegator (accAddr) when the
			// configured withdraw address cannot receive funds, but that fallback can
			// itself be unable to receive — e.g. accAddr is an EVM address whose Sei
			// mapping was re-associated to a different address, so CanAddressReceive
			// rejects it. This hook runs in EndBlock, so attempting the send and
			// panicking on the resulting bank error would halt the chain. Check
			// receivability first: when the recipient cannot receive, route the
			// commission to the community pool instead. The coins already back the
			// distribution module account (where community pool funds are held), so this
			// conserves value and avoids the partial module-account debit that a failed
			// SendCoins leaves behind.
			if h.k.canReceiveWithdrawAddr(ctx, withdrawAddr) {
				if err := h.k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, withdrawAddr, coins); err != nil {
					panic(err)
				}
			} else {
				feePool := h.k.GetFeePool(ctx)
				decCoins, err := sdk.NewDecCoinsFromCoins(coins...)
				if err != nil {
					panic(err)
				}
				feePool.CommunityPool = feePool.CommunityPool.Add(decCoins...)
				h.k.SetFeePool(ctx, feePool)
			}
```

**File:** sei-cosmos/x/distribution/keeper/store.go (L10-22)
```go
// get the delegator withdraw address, defaulting to the delegator address
func (k Keeper) GetDelegatorWithdrawAddr(ctx sdk.Context, delAddr sdk.AccAddress) sdk.AccAddress {
	store := ctx.KVStore(k.storeKey)
	b := store.Get(types.GetDelegatorWithdrawAddrKey(delAddr))
	if b == nil {
		return delAddr
	}
	withdrawAddr := sdk.AccAddress(b)
	if !k.canReceiveWithdrawAddr(ctx, withdrawAddr) {
		return delAddr
	}
	return withdrawAddr
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
