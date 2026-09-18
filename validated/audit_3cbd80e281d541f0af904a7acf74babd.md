## Finding

### Title
`associatePubKey` precompile accepts a self-asserted public key without proof-of-possession, allowing forced third-party address association - ([File: precompiles/addr/addr.go])

### Summary
The `addr` precompile (`0x...1004`) exposes two ways to link an EVM address to a Sei address: `associate` (requires an ECDSA signature over a fixed message, cryptographically proving the caller controls the private key) and `associatePubKey`/`associatePublicKey` (accepts a raw, caller-supplied compressed public key with **no signature and no proof of possession**). Because a secp256k1 public key is not secret (it is recoverable from any signature the real owner has ever produced, on either the Cosmos or EVM side), any unprivileged transaction sender who has observed a target's public key can call `associatePubKey` on the target's behalf and force the association, the account pubkey write, and the balance migration to execute — all without the target's consent or any transaction from the target.

### Finding Description
`associatePublicKey` only validates that the submitted bytes parse as a valid secp256k1 public key; it never checks that the caller signed anything with the corresponding private key: [1](#0-0) 

This is structurally different from `associate`, which recovers the public key from an ECDSA signature over a fixed challenge message, proving possession of the private key before any state change occurs: [2](#0-1) 

Both code paths converge on `associateAddresses`, which only guards against re-associating an already-linked `seiAddr`, and then unconditionally writes the address mapping, sets the account pubkey, and migrates any coins/wei sitting under the EVM-cast Sei address to the derived `seiAddr`: [3](#0-2) [4](#0-3) 

The maintainers' own in-code comments confirm this exact primitive is dangerous elsewhere in the codebase: they explicitly pre-associate EIP-7702 authorities before execution *because* "SetCode creates a mutable direct-cast EVM->Sei mapping that a later `associatePubKey` call can remap, orphaning any staking/distribution state created under the direct-cast identity (which can then halt the chain via the distribution validator-removal hook)": [5](#0-4) [6](#0-5) 

A regression test explicitly reproduces the underlying scenario — a validator operator whose operator address is a "direct-cast" `sdk.AccAddress(evmAddr[:])`, later remapped away by `associatePubKey` (or `SetAddressMapping`) to a different true Sei address — and shows the account becomes unable to receive funds (`CanSendTo` becomes false) once remapped: [7](#0-6) 

While the distribution module's `AfterValidatorRemoved` hook has been hardened to fall back to the community pool instead of panicking when the withdraw address becomes unreceivable, this is a narrow, single-hook mitigation: [8](#0-7) 

The root authorization gap — that `associatePubKey` lets anyone force-associate *any* address they can obtain a public key for, without the target's consent or any accompanying signature — remains in the general (non-EIP-7702) path. Any account that has accumulated state under its EVM-cast Sei address (received funds, delegated, been set as a withdraw address, etc.) before ever calling `associate` itself can have that identity unilaterally remapped by a third party the moment its public key becomes observable (e.g., after its first Cosmos-native or EVM transaction reveals the pubkey via signature recovery).

### Impact Explanation
- Forced, unconsented `SendCoins`/`SendCoinsAndWei` migration of funds sitting at the EVM-cast Sei address to the pubkey-derived Sei address, triggered by a third party at a time of their choosing (fee/timing abuse, funds movement without consent).
- Forced overwrite of the on-chain account object (`SetPubKey`) for an address that never opted into EVM association.
- Remapping identity out from under any Cosmos-native state (validator operator address, delegator/withdraw address, module interactions) built on the direct-cast address, which the codebase's own tests confirm can render that address unable to receive funds — the one already-patched consequence of this is a validator halting the distribution EndBlock hook (only mitigated for that single hook, not the general precompile-level authorization gap).

### Likelihood Explanation
Trivially reachable: any address that has broadcast a single signed transaction (Cosmos or EVM) exposes its public key for recovery. No special privileges, no CosmWasm, no governance — just a normal EVM `associatePubKey` call from any account. This is a public, gas-paid, always-available RPC/precompile surface.

### Recommendation
Require proof of possession for `associatePubKey` just as `associate` does (e.g., require a signature over a challenge message signed by the private key corresponding to the submitted public key), or restrict `associatePubKey` so it can only be invoked by (or with an accompanying authenticated action from) the address being associated. At minimum, before allowing a remap of an EVM-cast address that already has non-trivial Cosmos-native state (bonded delegations, module account interactions, non-zero locked coins), require additional verification rather than allowing an unauthenticated pubkey submission to silently walk that identity away.

### Proof of Concept
1. Victim `V` creates a Sei account and uses their EVM-cast address `castAddr = sdk.AccAddress(evmAddr[:])` directly for staking/bank operations without ever calling `associate`.
2. `V` broadcasts any signed transaction (Cosmos-native or EVM), which exposes `V`'s secp256k1 public key to public observers via signature recovery.
3. Attacker `A` extracts `V`'s compressed public key and calls `addr.associatePubKey(pubKeyHex)` from their own account, paying gas — see `associatePublicKey` in [1](#0-0) .
4. `associateAddresses` executes unconditionally (no consent from `V` required), setting `SetAddressMapping(seiAddr, evmAddr)`, writing `V`'s pubkey to the account, and migrating any balance held at `castAddr` to `seiAddr` — see [4](#0-3) .
5. As demonstrated in [7](#0-6) , if `castAddr` had pre-existing Cosmos-native state (e.g., validator operator/withdraw address), it is now unreceivable, and only the already-patched distribution hook prevents a chain-halting panic — confirming the underlying precompile-level authorization gap is real and was serious enough to require targeted mitigation elsewhere in the codebase.

### Citations

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

**File:** precompiles/addr/legacy/v575/addr.go (L136-176)
```go
func (p PrecompileExecutor) associate(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) ([]byte, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, err
	}

	// v, r and s are components of a signature over the customMessage sent.
	// We use the signature to construct the user's pubkey to obtain their addresses.
	v := args[0].(string)
	r := args[1].(string)
	s := args[2].(string)
	customMessage := args[3].(string)

	rBytes, err := decodeHexString(r)
	if err != nil {
		return nil, err
	}
	sBytes, err := decodeHexString(s)
	if err != nil {
		return nil, err
	}
	vBytes, err := decodeHexString(v)
	if err != nil {
		return nil, err
	}

	vBig := new(big.Int).SetBytes(vBytes)
	rBig := new(big.Int).SetBytes(rBytes)
	sBig := new(big.Int).SetBytes(sBytes)

	// Derive addresses
	vBig = new(big.Int).Add(vBig, utils.Big27)

	customMessageHash := crypto.Keccak256Hash([]byte(customMessage))
	evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)
	if err != nil {
		return nil, err
	}
```

**File:** utils/helpers/associate.go (L34-55)
```go
func (p AssociationHelper) AssociateAddresses(ctx sdk.Context, seiAddr sdk.AccAddress, evmAddr common.Address, pubkey cryptotypes.PubKey, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if !castAddr.Equals(seiAddr) && p.accountKeeper.GetAccount(ctx, seiAddr) == nil {
		castAcc := p.accountKeeper.GetAccount(ctx, castAddr)
		castBaseAcc, ok := castAcc.(*authtypes.BaseAccount)
		if ok && castBaseAcc.GetPubKey() == nil && p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
			p.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(seiAddr, pubkey, castBaseAcc.GetAccountNumber(), castBaseAcc.GetSequence()))
		}
	}
	p.evmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	acc := p.accountKeeper.GetAccount(ctx, seiAddr)
	if acc == nil {
		acc = p.accountKeeper.NewAccountWithAddress(ctx, seiAddr)
	}
	if acc.GetPubKey() == nil {
		if err := acc.SetPubKey(pubkey); err != nil {
			return err
		}
		p.accountKeeper.SetAccount(ctx, acc)
	}
	return p.MigrateBalance(ctx, evmAddr, seiAddr, migrateUseiOnly)
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

**File:** sei-cosmos/x/distribution/keeper/keeper_test.go (L95-123)
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
```

**File:** sei-cosmos/x/distribution/keeper/hooks.go (L46-72)
```go
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
