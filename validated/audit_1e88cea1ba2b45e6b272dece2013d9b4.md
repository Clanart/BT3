### Title
Unrecoverable panic in `BeforeDelegationSharesModified` can halt the chain when a delegation's withdraw address becomes unreceivable due to EVM address re-association - ([File: sei-cosmos/x/distribution/keeper/hooks.go])

### Summary
`sei-cosmos/x/distribution/keeper/hooks.go`'s `BeforeDelegationSharesModified` hook calls the internal `withdrawDelegationRewards` and panics on any error it returns: [1](#0-0) 

`withdrawDelegationRewards` in turn calls `bankKeeper.SendCoinsFromModuleToAccount` directly against whatever address `GetDelegatorWithdrawAddr` returns, with no receivability pre-check (unlike the sibling `AfterValidatorRemoved` hook, which was patched to call `canReceiveWithdrawAddr` first): [2](#0-1) [3](#0-2) 

### Finding Description
Sei allows an EVM address's Sei-side mapping to be re-associated after it has already been used as a "direct-cast" account identity (e.g. as a delegator or validator operator address), via `associatePubKey`/`Associate` on the `addr` precompile, which is intentionally allowed as long as the account isn't already associated: [4](#0-3) 

Once an EVM address is re-associated (`SetAddressMapping`) to a *different* true Sei address, `CanAddressReceive`/`CanSendTo` starts rejecting the original direct-cast address as a fund recipient: [5](#0-4) 

The code base already recognizes this exact hazard for the validator-removal path and patched it (`AfterValidatorRemoved` now falls back to the community pool via `canReceiveWithdrawAddr` instead of panicking): [6](#0-5) [7](#0-6) 

The comment in the EVM ante path even documents the general bug class: a direct-cast mapping created (e.g. by a delegation under the direct-cast address) can later be remapped by `associatePubKey`, "orphaning any staking/distribution state created under the direct-cast identity (which can then halt the chain via the distribution validator-removal hook)": [8](#0-7) 

However, `BeforeDelegationSharesModified` — which fires synchronously during ordinary user transactions (`MsgDelegate`, `MsgBeginRedelegate`, `MsgUndelegate`, or the corresponding staking precompile calls) whenever a delegation's shares change — was not given the same fallback. It still directly panics on any `SendCoins` failure: [1](#0-0) 

Because this hook runs inside `DeliverTx`/`FinalizeBlock` for a normal staking message (not just EndBlock), an unprivileged user can construct a sequence that makes `withdrawDelegationRewards` fail deterministically for their own delegation:
1. Delegate to a validator using a delegator address that is the direct-cast Sei address of an EVM address the user controls (`sdk.AccAddress(evmAddr[:])`), accruing rewards.
2. Call `associatePubKey`/`Associate` on the `addr` precompile for that EVM address, re-associating it to the user's *true* pubkey-derived Sei address. This makes the delegator's original direct-cast address unable to receive funds (`CanAddressReceive` now returns false for it, since the associated address is no longer the address itself).
3. Submit another `MsgDelegate` (or any message that touches the same delegation, e.g. `MsgBeginRedelegate`/`MsgUndelegate`) — the staking module's `BeforeDelegationSharesModified` hook runs, calls `withdrawDelegationRewards`, which tries to `SendCoinsFromModuleToAccount` to the now-unreceivable withdraw address (default withdraw address is the delegator itself). `SendCoins` returns an error/blocks the send, `withdrawDelegationRewards` propagates it, and the hook `panic(err)`s.

Since every validator processes the same transaction deterministically, this panic occurs on every node processing the block — a full chain halt rather than a single-node crash, which is arguably more severe than the referenced MongoDB analog (a single primary crash during a narrow window). This is directly analogous to BIT-mongodb-2026-5170: an ordinary user, exploiting a state created during a specific window (before/after re-association, mirroring the sharded-promotion window), can crash node(s) processing consensus-critical work.

### Impact Explanation
A successful trigger causes `panic(err)` inside `BeforeDelegationSharesModified`, called from `DeliverTx`/transaction processing for common staking messages. Because the panic happens deterministically for all validators executing the same block, this is a validator/chain halt — the most severe possible outcome for this bug class, exceeding the "crash of default-configuration RPC nodes" bar and matching "validator halt" in the accepted-impact list.

### Likelihood Explanation
The prerequisite building blocks are all reachable by an ordinary, unprivileged actor with no special permissions:
- Delegating to a validator is a public `MsgDelegate` action.
- Re-associating an EVM address via `associatePubKey`/`Associate` is a public precompile call available to any EVM address holder that isn't already associated.
- Triggering `BeforeDelegationSharesModified` again (e.g., a second `MsgDelegate` to the same validator) is trivial.

The remaining uncertainty (not fully confirmed within the available tool budget) is whether other guards elsewhere in the staking/bank pipeline (e.g., `SetWithdrawAddr`'s `BlockedAddr`/`CanSendTo` checks, or any check on the *delegator* address itself at delegate-time) would prevent the delegator's own address from ever being direct-cast-unreceivable in this exact way, or whether `WithdrawAddrEnabled` defaults and default withdraw-address fallback logic (`GetDelegatorWithdrawAddr`) fully mirror the same fallback pattern used in `AfterValidatorRemoved`. A background engineer should verify `GetDelegatorWithdrawAddr`'s exact resolution logic and confirm the reproduction end-to-end in a test harness before treating this as fully proven, but the code-level asymmetry between the patched `AfterValidatorRemoved` hook and the unpatched `BeforeDelegationSharesModified` hook is clear and directly supported by the cited source.

### Recommendation
Apply the same defensive pattern used in `AfterValidatorRemoved` to `BeforeDelegationSharesModified` (and any other caller of `withdrawDelegationRewards` reachable from hooks that must not panic): before calling `SendCoinsFromModuleToAccount`, check `canReceiveWithdrawAddr` (or equivalent) on the resolved withdraw address, and if it cannot receive funds, route the rewards to the community pool (or otherwise handle gracefully) instead of propagating the error into a `panic`. Alternatively, harden `withdrawDelegationRewards` itself so that a `SendCoins` failure never surfaces as an uncaught panic through any hook path.

### Proof of Concept
Conceptual reproduction (would need to be built out as a Go test similar to the existing `TestAfterValidatorRemovedRoutesToCommunityPoolForUnreceivableValidator`):
1. Create delegator account `D` whose address equals `sdk.AccAddress(evmAddr[:])` for some EVM address `evmAddr` (direct-cast identity), and fund it.
2. `app.StakingKeeper.Delegate(ctx, D, amount, ..., validator, true)` — creates delegation, `BeforeDelegationCreated`/`AfterDelegationModified` run normally; rewards begin accruing.
3. Advance blocks so `D`'s delegation accrues nonzero rewards.
4. Call `app.EvmKeeper.SetAddressMapping(ctx, otherTrueSeiAddr, evmAddr)` (simulating `associatePubKey`) — now `CanAddressReceive`/`CanSendTo` returns `false` for `D`.
5. Call `app.StakingKeeper.Delegate(ctx, D, moreAmount, ..., validator, true)` again (or trigger any path invoking `BeforeDelegationSharesModified` for `D`/`validator`).
6. Assert: the call panics (via `require.Panics`) inside `distribution.Hooks.BeforeDelegationSharesModified` → `withdrawDelegationRewards` → `SendCoinsFromModuleToAccount`, whereas the intended, safe behavior (per the existing fix pattern in `AfterValidatorRemoved`) would be graceful fallback with `require.NotPanics`.

### Citations

**File:** sei-cosmos/x/distribution/keeper/hooks.go (L26-74)
```go
func (h Hooks) AfterValidatorRemoved(ctx sdk.Context, _ sdk.ConsAddress, valAddr sdk.ValAddress) {
	// fetch outstanding
	outstanding := h.k.GetValidatorOutstandingRewardsCoins(ctx, valAddr)

	// force-withdraw commission
	commission := h.k.GetValidatorAccumulatedCommission(ctx, valAddr).Commission
	if !commission.IsZero() {
		// subtract from outstanding
		outstanding = outstanding.Sub(commission)

		// split into integral & remainder
		coins, remainder := commission.TruncateDecimal()

		// remainder to community pool
		feePool := h.k.GetFeePool(ctx)
		feePool.CommunityPool = feePool.CommunityPool.Add(remainder...)
		h.k.SetFeePool(ctx, feePool)

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
		}
	}
```

**File:** sei-cosmos/x/distribution/keeper/hooks.go (L106-113)
```go
func (h Hooks) BeforeDelegationSharesModified(ctx sdk.Context, delAddr sdk.AccAddress, valAddr sdk.ValAddress) {
	val := h.k.stakingKeeper.Validator(ctx, valAddr)
	del := h.k.stakingKeeper.Delegation(ctx, delAddr, valAddr)

	if _, err := h.k.withdrawDelegationRewards(ctx, val, del); err != nil {
		panic(err)
	}
}
```

**File:** sei-cosmos/x/distribution/keeper/delegation.go (L286-293)
```go
	// add coins to user account
	if !finalRewards.IsZero() {
		withdrawAddr := k.GetDelegatorWithdrawAddr(ctx, del.GetDelegatorAddr())
		err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, withdrawAddr, finalRewards)
		if err != nil {
			return nil, err
		}
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

**File:** sei-cosmos/x/distribution/keeper/keeper.go (L86-96)
```go
func (k Keeper) canReceiveWithdrawAddr(ctx sdk.Context, withdrawAddr sdk.AccAddress) bool {
	// BlockedAddr mirrors the gate SendCoinsFromModuleToAccount actually enforces:
	// beyond the module-account blocklist it also rejects dynamically-derived
	// addresses such as the EVM coinbase addresses (an "evm_coinbase"-prefixed
	// address), which CanSendTo does not catch. Consulting it here keeps this
	// predicate in lockstep with the send, so AfterValidatorRemoved never concludes
	// an address is receivable and then panics on the resulting bank error.
	return !k.blockedAddrs[withdrawAddr.String()] &&
		!k.bankKeeper.BlockedAddr(withdrawAddr) &&
		k.bankKeeper.CanSendTo(ctx, withdrawAddr)
}
```

**File:** x/evm/ante/preprocess.go (L103-110)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
	p.associateAuthorizationAuthorities(ctx, msg, associateHelper)
```
