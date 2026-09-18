### Title
Unauthenticated third-party identity binding via `associatePubKey` allows forced/impersonated EVM↔Sei address association without proof of key ownership - (File: precompiles/addr/addr.go)

### Summary
The `addr` precompile at `0x0000000000000000000000000000000000001004` exposes two ways to permanently bind an EVM address to a Sei address: `associate` (requires an ECDSA signature `v,r,s` over a custom message, proving the caller controls the private key) and `associatePublicKey`/`associatePubKey` (takes a raw compressed public key and derives both addresses from it with **no signature or proof-of-control check at all**). [1](#0-0) [2](#0-1) 

### Finding Description
`associatePublicKey` decodes the hex-encoded pubkey argument, parses it with `btcec.ParsePubKey`, and directly derives `evmAddr`/`seiAddr` via `helpers.GetAddressesFromPubkeyBytes`, then calls `associateAddresses` — at no point is any signature checked to prove the transaction sender actually controls the private key corresponding to that pubkey: [3](#0-2) 

This is structurally the same class of bug as CVE-2026-11861: a component accepts an externally supplied identity claim (a pubkey / cname) as authoritative and performs a privileged action (permanent identity binding) without verifying the caller is the entity it claims to be. Any EVM/Sei public key is trivially recoverable by anyone from any prior signed transaction of the target account (ECDSA signatures leak the public key), so an attacker who has observed even one transaction from a victim can call `associatePubKey` with that pubkey to force the association on the victim's behalf, without the victim's participation or consent.

Once forced through `associateAddresses`, the mapping is written via `SetAddressMapping` and is permanent (`GetEVMAddress` is checked for existing associations to reject re-associate attempts), so the real owner cannot later re-associate under a different flow or correct/consent to the binding. [4](#0-3) 

Critically, `AssociateAddresses` also triggers `MigrateBalance`, which force-moves the "cast address" (`sdk.AccAddress(evmAddr[:])`) balance and wei balance into the target `seiAddr`, and removes the cast account if it becomes empty — all as a side effect of a call an attacker can trigger unilaterally on the victim's behalf: [5](#0-4) 

### Impact Explanation
This does not enable outright asset theft — the derived `seiAddr` still corresponds to the victim's own pubkey, so funds move to their own account, not the attacker's. However, it does allow:
- Forced, unauthorized state changes to a victim's on-chain identity/account bookkeeping (pubkey installed on their account, association permanently locked) without consent or awareness, at a time chosen by the attacker.
- Front-running the victim's own intended association flow, potentially with unwanted side effects (e.g., forcing balance migration and account removal at an attacker-chosen block, prior to any funds/approvals the user expected at the "cast" address).
- Because association is irreversible (blocked by the "already associated" check), the victim cannot correct or delay this binding once triggered by a third party.

This qualifies as a "permanent" and "unauthorized" state mutation performed on behalf of another address without their signature, matching the report's flagged category of "unauthorized transfer via precompile" in the loose sense that balance migration is compelled, though it stops short of fund theft to an attacker-controlled address.

### Likelihood Explanation
Trivial and highly likely to be exploitable: pubkeys are public once any transaction is broadcast (ECDSA/secp256k1 signatures always reveal or allow recovery of the signer's public key). Calling `associatePubKey` requires no special permission — any address can call the `addr` precompile. The test suite confirms `associatePubKey` is meant to be called by any funded EVM account with just a raw pubkey argument (note in `addr.spec.ts`: "the target needs no funds"). [6](#0-5) 

### Recommendation
Require the same proof-of-ownership for `associatePublicKey` as for `associate`: either require a signed challenge/message from the private key matching the supplied pubkey, or restrict `associatePublicKey` to only be callable by the address it targets (verify `caller`/tx signer corresponds to the derived `evmAddr`) before performing the association and balance migration.

### Proof of Concept
1. Observe any transaction signed by a victim's Sei/EVM key on-chain (any prior send, delegate, etc.) and recover the ECDSA public key from the signature (standard operation; `go-ethereum`/`btcec` provide recovery).
2. Compress the recovered public key to hex.
3. Call `addr.associatePubKey(compressedPubKeyHex)` from any funded account (attacker's own account, no relation to the victim) — see `precompiles/addr/addr.go` lines 205-236.
4. The precompile associates the victim's `evmAddr` and `seiAddr` and migrates the balance held at the byte-cast Sei address into the victim's canonical Sei address, all without any signature from the victim authorizing this specific action at this specific time — see `associateAddresses`/`AssociateAddresses` at `precompiles/addr/addr.go` lines 239-255 and `utils/helpers/associate.go` lines 34-83.

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

**File:** utils/helpers/associate.go (L34-83)
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

func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
}
```

**File:** integration_test/precompile_tests/precompiles/addr.spec.ts (L1-9)
```typescript
/**
 * addr precompile (0x…1004) — end-to-end association semantics against a live Sei chain.
 *
 * Association permanently links an EVM address to its pubkey-derived sei address, so
 * every positive case uses a fresh random wallet rather than consuming a pool slot.
 * The admin signs the transactions; the precompile derives the target account from
 * the signature / pubkey argument, so the target needs no funds.
 * Sections: happy path & state parity / error handling / dispatch semantics.
 */
```
