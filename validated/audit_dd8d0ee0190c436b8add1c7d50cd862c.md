### Title
Arbitrary, unstructured `customMessage` accepted as signed data in the `addr` precompile's `associate` function enables cross-chain/cross-dApp signature replay to force account association and balance migration - ([File: precompiles/addr/addr.go])

### Summary
The `addr` precompile's `associate` method (and its identical logic replicated across every legacy version directory) recovers a public key by hashing an attacker/caller-supplied `customMessage` string with plain `keccak256` and feeding it straight into ECDSA recovery, with no EIP-712 domain separator, no chain ID, and no nonce/replay-protection binding. [1](#0-0) 

### Finding Description
`associate` takes `v, r, s, customMessage` directly as calldata, hashes `customMessage` with `crypto.Keccak256Hash` (not an EIP-191/712 wrapper enforced on-chain — the `\x19Ethereum Signed Message:\n` envelope is only added client-side in the test helper, not validated by the precompile), and passes the hash into `helpers.GetAddresses` for `ecrecover`-based pubkey recovery: [2](#0-1) 

The recovered pubkey/address pair is then used to call `associateAddresses`, which invokes `AssociationHelper.AssociateAddresses` → `MigrateBalance`, which **automatically moves all spendable coins and wei balance** from the EVM-cast Sei address to the newly-associated Sei address and can even remove the source account: [3](#0-2) 

Because there is no domain separator binding this signature to the Sei chain ID, the contract address, or a specific "associate" typed structure, any raw signature a user has produced elsewhere over a string that happens to collide with a valid `customMessage`/hash preimage can be replayed here by anyone (the caller of `associate` need not be the signer). This is exactly the bug class in the referenced report: arbitrary bytes-as-signature with no chain ID / domain separator / nonce, enabling replay across chains and applications. The repo's own code comments acknowledge this exact class of risk for the *EIP-7702* association path (`utils/helpers/address.go` lines 143-188), where they explicitly re-validate chain ID to stop "a publicly-visible authorization... signed for another chain... replayed... to force-associate them — migrating their direct-cast balance and orphaning staking/distribution state." That mitigation is only applied to `AuthorityToPreAssociate`; the plain `associate`/`associatePubKey` precompile path has no equivalent protection. [4](#0-3) 

### Impact Explanation
Anyone who has ever seen a raw ECDSA `(v,r,s)` signature from a given EVM key over an arbitrary message (e.g., leaked from another chain, another dApp using the same signing format, or a phishing site mimicking the `ASSOCIATE_MESSAGE` format) can submit it to Sei's `associate` precompile to force-associate that victim's EVM address with a Sei address, unconditionally triggering `MigrateBalance`, which force-moves the victim's `usei`/wei/spendable balances at the cast address into the newly-associated account and can delete the cast `BaseAccount`. This is an unauthorized state-changing action taken on behalf of a third party without their consent within the current transaction/chain context — a form of forced/unauthorized transfer and freezing of the previous account's identity/pubkey binding (association is essentially permanent since re-association of an already-set account is guarded elsewhere but a first association can be front-run using a foreign signature).

### Likelihood Explanation
Any unprivileged EVM transaction sender can call this precompile at `0x0000000000000000000000000000000000001004` with an arbitrary previously-observed signature; no special privileges, precompile owner permission, or victim cooperation is required at call time (the victim only had to sign *something* at some point, on Sei or elsewhere, using the same raw-message format). The lack of a domain separator/chain ID/nonce is a straightforward and reachable design flaw, not requiring parallel-execution races or governance access.

### Recommendation
Adopt structured, domain-separated signing for `associate`: include the Sei chain ID, precompile/contract address, a purpose-specific type string, and (ideally) a nonce or the caller's own address inside the hashed payload, following EIP-712 rather than a bare `keccak256(customMessage)`. At minimum, enforce that `customMessage` must match a canonical, chain-ID-bound template (mirroring the chain-ID check already implemented for `AuthorityToPreAssociate` in `utils/helpers/address.go`) before recovering and acting on the pubkey.

### Proof of Concept
1. On any EVM-compatible chain (or in any application) get a victim to produce a raw ECDSA signature `(v, r, s)` over a message string `M` using their EVM private key (e.g., via a phishing dApp that requests a "personal_sign" of `M`, or reuse of a real signature the victim made elsewhere for an unrelated purpose that happens to match Sei's expected format).
2. Call Sei's `addr` precompile: `associate(v, r, s, M)` as `precompiles/addr/addr.go` `associate()` — the caller does **not** need to be the victim; anyone holding the leaked/observed signature can submit it.
3. `helpers.GetAddresses` recovers the victim's pubkey/EVM address/Sei address from `(v,r,s, keccak256(M))` with no domain/chain-ID check [5](#0-4) .
4. `associateAddresses` → `AssociationHelper.AssociateAddresses` → `MigrateBalance` executes automatically, moving the victim's cast-address `usei`/wei/spendable balances to the newly-set Sei address and potentially removing the cast account [6](#0-5) , all without the victim ever knowingly interacting with Sei.

### Citations

**File:** precompiles/addr/addr.go (L160-202)
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

**File:** utils/helpers/address.go (L44-61)
```go
func GetAddresses(V *big.Int, R *big.Int, S *big.Int, data common.Hash) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	pubkey, err := RecoverPubkey(data, R, S, V, true)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}

	return GetAddressesFromPubkeyBytes(pubkey)
}

func GetAddressesFromPubkeyBytes(pubkey []byte) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	evmAddr, err := PubkeyToEVMAddress(pubkey)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	seiPubkey := PubkeyBytesToSeiPubKey(pubkey)
	seiAddr := sdk.AccAddress(seiPubkey.Address())
	return evmAddr, seiAddr, &seiPubkey, nil
}
```

**File:** utils/helpers/address.go (L143-165)
```go
// AuthorityToPreAssociate returns the authority of an EIP-7702 authorization that should be
// associated with its true (pubkey-derived) Sei address before execution, or ok=false.
//
// It mirrors go-ethereum's StateTransition.validateAuthorization (chain id, nonce overflow,
// authority code, and account-nonce checks) so that pre-association happens only for
// authorizations the EVM will actually apply, and additionally skips authorities that are
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
```
