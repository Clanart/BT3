### Title
`associatePubKey` allows unauthorized re-association of a validator/delegator's direct-cast address, permanently misrouting bank/staking/distribution funds - ([File: precompiles/addr/addr.go])

### Summary
The `addr` precompile's `associatePubKey` method links an arbitrary EVM address to its pubkey-derived Sei address **without any proof of private-key ownership** — unlike `associate`, which requires a valid ECDSA signature. Any caller who knows a target's raw public key (which is public information, recoverable from any Cosmos-SDK signature/broadcast tx) can call `associatePubKey` to permissionlessly move the EVM↔Sei mapping away from the "direct-cast" address that funds, delegations, or validator/withdraw records were created under. This is directly analogous to the Discourse bug class: an operation that should require the acting identity's authorization is instead performed on behalf of another identity due to missing access control, causing state to be attributed/misrouted incorrectly.

### Finding Description
`associatePubKey` derives `evmAddr`/`seiAddr` purely from a supplied public key, with no signature check over any message, and then calls `associateAddresses` → `AssociateAddresses`, which unconditionally re-points the EVM→Sei mapping and migrates the cast address's balance: [1](#0-0) 

Compare to `associate`, which requires v/r/s over a signed message to prove key possession: [2](#0-1) 

The only guard in `associateAddresses` is that the *seiAddr* must not already be associated — it does not require that the caller controls the seiAddr or evmAddr being associated: [3](#0-2) 

Before association, any EVM address has an implicit "direct-cast" Sei address (`common.BytesToAddress`/`sdk.AccAddress(evmAddr[:])`), which can already hold funds, be a validator operator, or be a delegator/withdraw address: [4](#0-3) 

Once `associatePubKey` re-maps the EVM address to a *different* true Sei address, `CanAddressReceive`/`CanSendTo` on the old direct-cast address flips to false — it becomes permanently unreceivable: [5](#0-4) 

The code base's own comments and tests confirm the severity of this exact primitive: a later `associatePubKey` call can "remap" a direct-cast identity and orphan staking/distribution state tied to it, which can halt the chain via the distribution validator-removal hook unless specially handled: [6](#0-5) [7](#0-6) 

The distribution module had to add defensive fallback logic specifically to avoid panicking (halting EndBlock) when a validator's withdraw address becomes unreceivable due to this exact re-association: [8](#0-7) [9](#0-8) 

While EIP-7702 `SetCode` authorities were explicitly hardened against this by pre-associating them to their true pubkey-derived address before delegation code is installed (and by requiring `AuthorityToPreAssociate` to check the authority isn't already associated / cannot be re-mapped): [10](#0-9) 

...the general `associatePubKey` precompile entrypoint itself remains permissionless and signature-free for **any** direct-cast address that has not yet been associated — including validator operator addresses, delegator addresses, and plain fund-holding cast addresses — since nothing prevents an attacker who has observed a target's public key from calling `associatePubKey` on that key before the target does.

### Impact Explanation
- Funds sent to (or accrued under) a direct-cast address — including staking/distribution flows such as commission and reward withdraw addresses — can be permanently rerouted to the pubkey-derived Sei address chosen by whoever calls `associatePubKey` first, without the direct-cast address owner's consent.
- Because the resulting mapping makes the *old* direct-cast address unable to receive funds (`CanAddressReceive` returns false once re-associated to a different true address), any subsequent code path that assumes it can still route funds there (bank transfers, distribution withdraw, validator commission) can either freeze funds (rerouted to community pool as a fallback, which is a form of fund misdirection/loss for the rightful party) or, in code paths without that defensive fallback, trigger a panic during `EndBlock`, which is a validator/chain halt.
- The bug class matches the reported analog precisely: an operation ("associate an identity") is performed as/for another user's identity without verifying the caller is authorized to act for that identity — mirroring Discourse's embeddable-comments flaw where topics were created "as any user" due to missing authorization checks.

### Likelihood Explanation
Public keys are not secret: they are exposed the moment an account broadcasts any signed Cosmos or EVM transaction (ECDSA public keys are recoverable from signatures) or when validators publish their consensus/operator pubkeys. Any unprivileged party can extract a target's pubkey and call `associatePubKey` via a plain EVM transaction to the `0x1004` precompile before the legitimate owner ever associates. No special privileges, contract deployment, or complex setup are required — only knowledge of a public key and a normal transaction, making this readily reachable by any transaction sender.

### Recommendation
Require proof-of-possession for `associatePubKey` the same way `associate` does (a signature over a fresh, precompile-specific message), or otherwise gate re-association of already-fund/state-bearing direct-cast addresses (e.g., validator operator addresses, addresses with non-zero delegations, addresses currently configured as withdraw addresses) behind the same authenticated flow used for EIP-7702 authorities (`AuthorityToPreAssociate`-style checks), so that an unrelated third party cannot unilaterally move another user's or validator's identity mapping.

### Proof of Concept
1. Observe any broadcast Cosmos/EVM transaction from a validator operator or fund-holding account and recover its ECDSA public key (trivial from any ECDSA signature).
2. Before the account ever calls `associate`/sends an EVM tx itself, call `addr.associatePubKey(pubKeyHex)` from an unrelated attacker-controlled account.
3. `associateAddresses` executes successfully (only guard is that the *target* seiAddr isn't already associated), remapping `evmAddr` away from its direct-cast Sei address, migrating any balance held at the cast address, and — if the cast address was a validator operator/withdraw address — leaving that address permanently unable to receive funds per `CanAddressReceive`, as demonstrated by: [11](#0-10)

### Citations

**File:** precompiles/addr/addr.go (L160-203)
```go
func (p PrecompileExecutor) associate(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, 0, err
	}

	// v, r and s are components of a signature over the customMessage sent.
	// We use the signature to construct the user's pubkey to obtain their addresses.
	v := args[0].(string)
	r := args[1].(string)
	s := args[2].(string)
	customMessage := args[3].(string)

	rBytes, err := decodeHexString(r)
	if err != nil {
		return nil, 0, err
	}
	sBytes, err := decodeHexString(s)
	if err != nil {
		return nil, 0, err
	}
	vBytes, err := decodeHexString(v)
	if err != nil {
		return nil, 0, err
	}

	vBig := new(big.Int).SetBytes(vBytes)
	rBig := new(big.Int).SetBytes(rBytes)
	sBig := new(big.Int).SetBytes(sBytes)

	// Derive addresses
	vBig = new(big.Int).Add(vBig, utils.Big27)

	customMessageHash := crypto.Keccak256Hash([]byte(customMessage))
	evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
}
```

**File:** precompiles/addr/addr.go (L205-236)
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

**File:** x/evm/keeper/address.go (L41-64)
```go
func (k *Keeper) GetEVMAddressOrDefault(ctx sdk.Context, seiAddress sdk.AccAddress) common.Address {
	addr, ok := k.GetEVMAddress(ctx, seiAddress)
	if ok {
		return addr
	}
	return common.BytesToAddress(seiAddress)
}

func (k *Keeper) GetSeiAddress(ctx sdk.Context, evmAddress common.Address) (sdk.AccAddress, bool) {
	store := ctx.KVStore(k.storeKey)
	bz := store.Get(types.EVMAddressToSeiAddressKey(evmAddress))
	if bz == nil {
		return []byte{}, false
	}
	return bz, true
}

func (k *Keeper) GetSeiAddressOrDefault(ctx sdk.Context, evmAddress common.Address) sdk.AccAddress {
	addr, ok := k.GetSeiAddress(ctx, evmAddress)
	if ok {
		return addr
	}
	return sdk.AccAddress(evmAddress[:])
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

**File:** sei-cosmos/x/distribution/keeper/hooks.go (L44-64)
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
```

**File:** utils/helpers/address.go (L149-188)
```go
// already associated. Mirroring validateAuthorization is essential to security: the
// authorization sig hash is computed from the auth's own ChainID, so recovery and
// auth.Authority() succeed for an authorization signed for ANY chain. Without these checks a
// publicly-visible authorization a user signed for another chain (e.g. Ethereum mainnet)
// could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their
// direct-cast balance and orphaning staking/distribution state — even though the EVM skips
// the wrong-chain authorization and installs no delegation.
func AuthorityToPreAssociate(ctx sdk.Context, k AuthorizationStateReader, auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, bool) {
	// Chain ID must be null or match the local chain.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.ChainID(ctx)) != 0 {
		return common.Address{}, nil, nil, false
	}
	// Nonce must not overflow (EIP-2681).
	if auth.Nonce+1 < auth.Nonce {
		return common.Address{}, nil, nil, false
	}
	evmAddr, seiAddr, pubkey, err := RecoverAddressesFromAuthorization(auth)
	if err != nil {
		return common.Address{}, nil, nil, false
	}
	// Cross-check against go-ethereum's authoritative recovery so we only ever act on the
	// exact address SetCode would target during execution.
	if authAddr, aerr := auth.Authority(); aerr != nil || authAddr != evmAddr {
		return common.Address{}, nil, nil, false
	}
	// Authority must have no code, or only an existing delegation designator.
	if code := k.GetCode(ctx, evmAddr); len(code) != 0 {
		if _, ok := ethtypes.ParseDelegation(code); !ok {
			return common.Address{}, nil, nil, false
		}
	}
	// Authority account nonce must match the authorization nonce.
	if k.GetNonce(ctx, evmAddr) != auth.Nonce {
		return common.Address{}, nil, nil, false
	}
	// Already-associated authorities need no pre-association (and cannot be re-mapped).
	if _, associated := k.GetEVMAddress(ctx, seiAddr); associated {
		return common.Address{}, nil, nil, false
	}
	return evmAddr, seiAddr, pubkey, true
```
