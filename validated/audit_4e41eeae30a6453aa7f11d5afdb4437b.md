## Analysis

The GHSA report describes a class of bug where a reconnect path silently applies a scope/identity change (`operator.read` → `operator.admin`) without requiring an explicit, freshly-authorized approval — i.e., a caller-identity check is missing where the design assumes proof of possession/consent. Sei-chain has a directly analogous gap in the `addr` precompile's account-association machinery.

### The gap

The `addr` precompile exposes two "bind my EVM/Sei identity" transactions: `associate` (proves possession via an ECDSA signature `v,r,s` over a custom message) and `associatePubKey` (takes a raw, unauthenticated public key with **no signature or proof of possession at all**): [1](#0-0) 

Both funnel into `associateAddresses`, whose only guard is that the *target Sei address* isn't already associated — it never checks whether the caller actually controls the private key behind the derived addresses, nor whether the derived `evmAddr` is already mapped to a *different* Sei address: [2](#0-1) 

Critically, the precompile's `Execute` entry point discards the EVM `caller` address entirely (`_ common.Address`), so there is no comparison between `caller` and the address derived from the supplied key material: [3](#0-2) 

Since a public key is recoverable from any transaction signature a victim has ever broadcast, **any unprivileged party can call `associatePubKey` with a victim's known public key** to force an address association on the victim's behalf, at a time of the attacker's choosing — without the victim submitting anything or approving it in that moment.

### Why this matters (documented elsewhere in the same codebase)

The codebase's own comments and tests acknowledge that forcing this association at the wrong time can permanently orphan state. The `SetCode`/EIP-7702 ante-handler fix explicitly describes the same remap mechanism as dangerous: [4](#0-3) 

And a dedicated regression test demonstrates the resulting freeze: once an EVM address backing a validator/delegator's direct-cast identity is re-pointed via `associatePubKey`, that identity becomes permanently unable to receive funds, and the distribution keeper has to special-case route commission to the community pool to avoid a panic: [5](#0-4) 

The EIP-7702 fix only pre-associates *authorization authorities* before `associatePubKey` can be abused in that one code path — it does not add a caller-identity check to `associatePubKey` itself, so the precompile remains callable by anyone with knowledge of a victim's public key, for any account (not just SetCode authorities).

### Title
Unauthenticated `associatePubKey` precompile call lets any sender force address (re)association for another account, permanently orphaning delegator/validator state - (File: precompiles/addr/addr.go)

### Summary
The `addr` precompile's `associatePubKey` method (`0x0000000000000000000000000000000000001004`) accepts a bare public key and derives the Sei/EVM address pair from it, but neither `Execute` nor `associateAddresses` verifies that the transaction's actual `caller` controls the corresponding private key. Because ECDSA public keys are trivially recoverable from any prior signed transaction, any address on-chain can force an association for a victim's EVM address by resubmitting the victim's already-public key — at a time chosen by the attacker rather than the victim.

### Finding Description
- `Execute` receives the EVM `caller` but discards it (`_ common.Address`) for both `associate` and `associatePubKey`: [3](#0-2) 
- `associatePublicKey` derives `evmAddr`/`seiAddr` solely from an attacker-suppliable compressed public key, with no signature check proving the caller possesses the matching private key: [1](#0-0) 
- `associateAddresses` only rejects the call if the *target* Sei address already has an association; it does not check whether `evmAddr` already carries a mapping to a *different* Sei address before overwriting it: [2](#0-1) 
- This exact "later `associatePubKey` call can remap ... orphaning staking/distribution state" hazard is called out by the maintainers themselves in the EIP-7702 ante-handler comments: [4](#0-3) 
- The consequence — an address becoming permanently unable to receive its own funds/commission — is demonstrated by the existing regression test for validator removal: [5](#0-4) 

Unlike `associate` (which requires a valid ECDSA signature `v,r,s` over `customMessage`, i.e., actual proof of private-key possession), `associatePubKey` performs no such proof, breaking the implicit authorization invariant that only the key owner can bind/rebind their own address — directly analogous to the reported CWE-863 class where an identity-widening operation is accepted without a fresh, explicit authorization check.

### Impact Explanation
An attacker who has observed any signed transaction, authorization, or otherwise obtained a victim's raw public key (which is not secret — it is exposed by every ECDSA signature) can call `associatePubKey` on the victim's behalf. This lets the attacker:
- Force the victim's `EVM->Sei` mapping to change at an attacker-chosen time, ahead of the victim's own intended association.
- Trigger `MigrateBalance`'s side effects prematurely/at a disruptive moment.
- In the specific but realistic scenario where the victim's EVM address already backs a direct-cast validator/delegator identity (created before the victim ever explicitly associates), force the remap that orphans that identity's outstanding staking/distribution state, matching the "permanent freezing" impact bar — the codebase's own test shows the resulting commission becomes permanently unwithdrawable by the rightful validator and must be diverted to the community pool.

### Likelihood Explanation
High reachability: `associatePubKey` is a public, unauthenticated precompile transaction reachable by any EVM caller with no special privileges, gas cost is a flat 50,000, and the only "secret" required (the public key) is not secret at all — it circulates publicly with every signed transaction from the victim.

### Recommendation
Require that the EVM `caller` passed into `Execute`/`associatePublicKey` equals the `evmAddr` derived from the supplied public key (i.e., only self-association is allowed via `associatePubKey`), or alternatively require the same ECDSA proof-of-possession that `associate` already enforces. Additionally, `associateAddresses` should reject associations where `evmAddr` is already mapped to a different Sei address, rather than only checking the target Sei address side.

### Proof of Concept
1. Observe any transaction signed by victim `V` (mempool, block explorer, or a wasm/EVM tx `V` sent), and recover `V`'s uncompressed secp256k1 public key from the ECDSA signature (standard `ecrecover`).
2. As attacker `A` (any unprivileged account), call `addr.associatePubKey(V_pubkey_hex)` from any EVM address.
3. `Execute` ignores `A`'s caller address entirely; `associatePublicKey` derives `evmAddr_V`/`seiAddr_V` from the supplied key and calls `associateAddresses`, which succeeds as long as `seiAddr_V` has no existing association — regardless of who submitted the transaction.
4. If `evmAddr_V` previously backed a direct-cast validator/delegator identity (`sdk.AccAddress(evmAddr_V[:])`), the mapping is overwritten to `seiAddr_V`, orphaning that identity's outstanding rewards/commission exactly as demonstrated by `TestAfterValidatorRemovedRoutesToCommunityPoolForUnreceivableValidator` [6](#0-5) , but triggered non-consensually by `A` instead of `V`.

### Citations

**File:** precompiles/addr/addr.go (L93-117)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, _ common.Address, _ common.Address, args []interface{}, value *big.Int, readOnly bool, _ *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	// Needed to catch gas meter panics
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("execution reverted: %v", r)
		}
	}()
	switch method.Name {
	case GetSeiAddressMethod:
		return p.getSeiAddr(ctx, method, args, value)
	case GetEvmAddressMethod:
		return p.getEvmAddr(ctx, method, args, value)
	case Associate:
		if readOnly {
			return nil, 0, errors.New("cannot call associate precompile from staticcall")
		}
		return p.associate(ctx, method, args, value)
	case AssociatePubKey:
		if readOnly {
			return nil, 0, errors.New("cannot call associate pub key precompile from staticcall")
		}
		return p.associatePublicKey(ctx, method, args, value)
	}
	return
}
```

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

**File:** sei-cosmos/x/distribution/keeper/keeper_test.go (L95-147)
```go
// TestAfterValidatorRemovedRoutesToCommunityPoolForUnreceivableValidator covers the
// case where the validator operator address itself cannot receive funds — its EVM
// address was re-associated (e.g. via associatePubKey) away from the direct-cast Sei
// address it was created under. The withdraw-address fallback in GetDelegatorWithdrawAddr
// resolves back to that same unreceivable operator address, so the commission
// force-withdraw fails. AfterValidatorRemoved runs during EndBlock, so it must not panic:
// the commission is routed to the community pool instead, which conserves value because
// the coins already back the distribution module account.
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
```
