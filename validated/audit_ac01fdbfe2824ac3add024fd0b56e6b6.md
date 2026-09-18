### Title
`associate()` accepts a valid signature over any attacker-chosen message and reuses it as proof-of-ownership for an unrelated purpose (Sei/EVM address association + fund migration) - (File: `precompiles/addr/addr.go`)

### Summary
The `addr` precompile's `associate(v, r, s, customMessage)` method recovers a pubkey/address from an EIP-191 `personal_sign`-style signature over an arbitrary, caller-supplied `customMessage` and, if the recovered Sei address is unassociated, immediately binds it to the derived EVM address and migrates all funds from the EVM-cast account to it. There is no on-chain check that `customMessage` equals the intended association phrase (e.g. "Please sign this message to link your EVM and Sei addresses...") — the message content is purely a client-side UI convention that the contract never verifies. [1](#0-0) [2](#0-1) 

This is the same bug class as the aria2c EKU report: a credential (signature) that is only ever supposed to be valid for one purpose (whatever off-chain application solicited the `personal_sign`) is accepted on-chain for a completely different, security-sensitive purpose (permanently binding identity + moving funds), because the code that consumes the credential never checks that it was scoped/intended for that purpose.

### Finding Description
`associate()` takes `v`, `r`, `s` and `customMessage`, hashes `customMessage` with `keccak256`, and calls `helpers.GetAddresses` to `ecrecover` a pubkey directly from that hash and signature. [3](#0-2) [4](#0-3) 

The recovered pubkey then drives `associateAddresses`, which calls `AssociationHelper.AssociateAddresses`. This sets the EVM↔Sei address mapping, sets the account pubkey, and — critically — calls `MigrateBalance`, which unconditionally transfers *all* spendable coins and wei balance from the EVM-cast account (`sdk.AccAddress(evmAddr[:])`) to the newly bound Sei address, and removes the old account. [5](#0-4) [6](#0-5) 

Nothing in this path validates that `customMessage`:
- matches a fixed, association-specific string, or
- includes any domain separator tying it to Sei chain / the `associate` action / a nonce.

Because `ecrecover` succeeds for *any* message+signature pair regardless of content, any valid EIP-191 signature a user has ever produced for a totally unrelated purpose (logging into some other dApp with `personal_sign`, agreeing to arbitrary terms, signing a message for a different chain/app that happens to use the same signature scheme) can be captured by anyone (it is not secret — signatures and messages are routinely displayed/logged/relayed off-chain) and replayed by an unrelated third party as the `v/r/s/customMessage` arguments to `associate()`. The precompile has no `msg.sender` requirement tying the transaction sender to the recovered signer, so anyone can submit this on the victim's behalf. [7](#0-6) 

Notably, the codebase itself documents an almost identical concern for a *different* signing path (EIP-7702 authorizations) and adds explicit chain-ID and authoritative-recovery cross-checks specifically to prevent "a publicly-visible authorization a user signed for another chain... being replayed... to force-associate them — migrating their direct-cast balance," per the comment on `AuthorityToPreAssociate`. [8](#0-7)  The `associate()` precompile method, however, has no analogous protection: no chain-ID binding, no fixed/expected message check, and no replay/purpose scoping on the `customMessage` field.

### Impact Explanation
Any third party who obtains a personal-sign signature the victim produced for an unrelated purpose can force, without the victim's consent or awareness:
1. Permanent binding of the victim's EVM address to their Sei address (irreversible via this precompile, since it errors if already associated). [9](#0-8) 
2. An unauthorized, unconditional transfer of all spendable balance and wei balance from the victim's EVM-cast account to the newly associated Sei account, and removal of the old account. [10](#0-9) 

This is a forced, third-party-triggered on-chain state mutation and fund transfer performed via a public-EVM-precompile call, executed without any binding between the signed message and the specific action being authorized — satisfying "unauthorized transfer via precompile" from the impact criteria. Even though the source and destination of the migrated funds are ultimately controlled by the same private key, the transfer is executed at a time and in a manner the victim did not choose or consent to, and is irreversible once triggered (the "already associated" guard blocks the true owner from re-doing it correctly afterward, e.g. with a proper message or different flow).

### Likelihood Explanation
Reachability is trivial: `associate` is a standard EVM precompile call reachable by any unprivileged transaction sender at `0x0000000000000000000000000000000000001004`, requires no special privilege, and needs only a signature+message pair the attacker observed anywhere (e.g., another dApp's login flow, a public log, a different chain's `personal_sign` prompt). No cryptographic weakness is required — this is a straightforward missing purpose-check on a signature verification path, and the same code has been present unchanged across many precompile versions (`v575` through the current `precompiles/addr/addr.go`). [11](#0-10) 

### Recommendation
Enforce that `customMessage` matches a fixed, on-chain-verified association string that includes chain ID and/or contract address as a domain separator (mirroring the explicit chain-ID check already added for EIP-7702 authorizations in `AuthorityToPreAssociate`), so a signature cannot be reused across purposes or chains. Consider also requiring the transaction sender to match some expected relationship to the recovered address, or otherwise scoping the credential so it can only be used for the `associate` action it was created for.

### Proof of Concept
1. Victim signs an arbitrary message `M` with `personal_sign` for any unrelated off-chain purpose (e.g., logging into a web app), producing `(v, r, s)` over `keccak256(EIP-191(M))`.
2. Attacker observes this `(v, r, s, M)` tuple (from a network log, a malicious/compromised dApp, or any public source).
3. Attacker (any address, no special permission) submits a Sei EVM transaction calling `associate(v, r, s, M)` on `0x...1004`.
4. `helpers.GetAddresses` recovers the victim's EVM address, Sei address, and pubkey purely from `(v, r, s, keccak256(M))`. [12](#0-11) 
5. `associateAddresses` → `AssociateAddresses` binds the addresses and calls `MigrateBalance`, transferring all of the victim's EVM-cast account balance to the new Sei address, permanently and without the victim ever intending to trigger Sei address association. [10](#0-9)

### Citations

**File:** precompiles/addr/addr.go (L105-109)
```go
	case Associate:
		if readOnly {
			return nil, 0, errors.New("cannot call associate precompile from staticcall")
		}
		return p.associate(ctx, method, args, value)
```

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

**File:** contracts/test/EVMPrecompileTest.js (L69-78)
```javascript
            
            const message = `Please sign this message to link your EVM and Sei addresses. No SEI will be spent as a result of this signature.\n\n`;
            const messageLength = Buffer.from(message, 'utf8').length;
            const signatureHex = await unassociatedWallet.signMessage(message);

            const sig = hre.ethers.Signature.from(signatureHex);
            
            const appendedMessage = `\x19Ethereum Signed Message:\n${messageLength}${message}`;
            const associatedAddrs = await addr.associate(`0x${sig.v-27}`, sig.r, sig.s, appendedMessage)
            const addrs = await associatedAddrs.wait();
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

**File:** utils/helpers/address.go (L143-188)
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

**File:** precompiles/addr/legacy/v575/addr.go (L136-192)
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

	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}

	// Associate Addresses:
	associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.bankKeeper, p.accountKeeper)
	err = associationHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey)
	if err != nil {
		return nil, err
	}

	return method.Outputs.Pack(seiAddr.String(), evmAddr)
}
```
