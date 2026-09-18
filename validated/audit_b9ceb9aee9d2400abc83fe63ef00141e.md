### Title
Signature-domain-confusion in the `addr` precompile's `associate()` allows forced, non-consensual address association and balance migration - ([File: precompiles/addr/addr.go])

### Summary
The `associate` method of the EVM `addr` precompile (`0x0000000000000000000000000000000000001004`) recovers a public key from a caller-supplied `(v, r, s, customMessage)` tuple and immediately force-associates the recovered EVM/Sei address pair, migrating balances and installing the pubkey on the target account. There is no on-chain binding of `customMessage` to an association-specific domain (chain ID, contract address, or fixed template string) — the contract simply hashes whatever bytes the caller passes and treats any valid ECDSA signature over those bytes as proof of intent to associate.

### Finding Description
`PrecompileExecutor.associate` in [1](#0-0)  takes `v`, `r`, `s`, and an arbitrary `customMessage` string directly from `args`, computes `customMessageHash := crypto.Keccak256Hash([]byte(customMessage))`, and calls `helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)` to recover the signer's pubkey via `ecrecover`, per [2](#0-1) . The result is passed straight into `associateAddresses`, which calls `AssociationHelper.AssociateAddresses` — this sets the address mapping, installs the pubkey on the Sei account, and unconditionally migrates the caster address's spendable coins and wei balance to the newly associated `seiAddr`, per [3](#0-2) .

Nowhere in this path is `customMessage` validated to be an association-specific message (e.g. the "Please sign this message to link your EVM and Sei addresses..." template used only client-side in tests, per [4](#0-3) ). The EIP-191 `"\x19Ethereum Signed Message:\n<len>"` prefix, if any, is also applied client-side before being sent as `customMessage` — the precompile hashes the raw bytes it is given with no format enforcement. Consequently, **any** valid personal-sign-style signature a victim has ever produced for any purpose (logging into an unrelated web app, signing a message for a different chain/dApp, etc.), together with the plaintext of that message (which is very frequently publicly disclosed, e.g. shown in wallet UIs, dApp backends, or on other explorers), can be replayed by an unrelated third party to call `associate()` on the victim's behalf.

This is the same bug class as CVE-2016-2118 (BADLOCK): authentication data intended for one protocol context/session is accepted and acted upon without being cryptographically bound to the specific action/domain it is being used to authorize, enabling an unauthorized party to trigger a state-changing/impersonation-equivalent action using data captured from elsewhere. Sei's own codebase explicitly identifies and defends against this exact risk for EIP-7702 authorizations: `AuthorityToPreAssociate` deliberately checks the authorization's `ChainID` before pre-associating, with an explicit comment warning that a "publicly-visible authorization a user signed for another chain ... could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their direct-cast balance", per [5](#0-4) . The `addr` precompile's `associate()` path has no equivalent domain check on `customMessage`, so the same attack the codebase engineered around for EIP-7702 remains open here.

### Impact Explanation
Because `AssociateAddresses`/`MigrateBalance` unconditionally moves the victim's usei, wei, and other spendable coin balances held at the direct-cast address (`sdk.AccAddress(evmAddr[:])`) to the newly linked Sei address and installs a pubkey on the account, per [6](#0-5) , an attacker can force this migration and pubkey-installation to happen at a time and in a manner the victim did not intend, using a signature the victim produced for an unrelated purpose. This is an unauthorized state-changing action ("unauthorized transfer via precompile") triggered without the victim's consent for association, and it also forecloses the victim's own ability to later associate cleanly (the "already associated" guard in `associateAddresses`, per [7](#0-6) , will now reject any association the victim tries to perform themselves with different intended semantics).

### Likelihood Explanation
Exploitation requires only a `v, r, s` signature and its exact signed plaintext produced by the victim's EVM key for any purpose — no chain-specific or association-specific context is required by the contract. Given wallets are frequently reused across dApps and personal-sign messages/plaintexts are commonly surfaced (in dApp backends, support tickets, other explorers, or simply requested by a malicious dApp under an innocuous pretext), obtaining such a signature from an unrelated context is realistic and requires no special network position, making this reachable by any public RPC caller with no privileged access.

### Recommendation
Bind `customMessage` (or the hash presented to `ecrecover`) to a fixed, non-reusable domain before accepting it in `associate()`: e.g., require the message to match a strict, on-chain-validated template that embeds the chain ID and the caller's target Sei/EVM address, reject signatures whose message doesn't match, and/or require a nonce so a given signature can only be consumed once and only for this specific purpose — mirroring the chain-ID binding that `AuthorityToPreAssociate` already performs for EIP-7702 authorizations.

### Proof of Concept
1. Victim signs an unrelated personal-sign message `M` with their EVM key on any dApp/site (`M` and the resulting `(v,r,s)` become known/observable to the attacker, e.g. via a malicious dApp, log, or public disclosure).
2. Attacker computes the EIP-191 envelope for `M` client-side (`"\x19Ethereum Signed Message:\n" + len(M) + M`) exactly as the legitimate flow does in [4](#0-3) , and submits `addr.associate(v-27, r, s, envelope)` from their own account.
3. `PrecompileExecutor.associate` recovers the victim's real pubkey/addresses from the signature and hash of the attacker-submitted envelope, per [8](#0-7) , and `associateAddresses`/`AssociateAddresses` force-links the victim's EVM/Sei addresses and migrates the victim's cast-address balance, per [3](#0-2) , all without the victim ever calling `associate()` themselves or intending an association.

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

**File:** precompiles/addr/addr.go (L239-244)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}
```

**File:** utils/helpers/address.go (L44-81)
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

// first half of go-ethereum/core/types/transaction_signing.go:recoverPlain
func RecoverPubkey(sighash common.Hash, R, S, Vb *big.Int, homestead bool) ([]byte, error) {
	if Vb.BitLen() > 8 || Vb.Uint64() < 27 {
		return []byte{}, ethtypes.ErrInvalidSig
	}
	V := byte(Vb.Uint64() - 27) //nolint:gosec // the bit-length and lower-bound checks make the subtraction fit in one byte.
	if !crypto.ValidateSignatureValues(V, R, S, homestead) {
		return []byte{}, ethtypes.ErrInvalidSig
	}
	// encode the signature in uncompressed format
	r, s := R.Bytes(), S.Bytes()
	sig := make([]byte, crypto.SignatureLength)
	copy(sig[32-len(r):32], r)
	copy(sig[64-len(s):64], s)
	sig[64] = V

	// recover the public key from the signature
	return crypto.Ecrecover(sighash[:], sig)
}
```

**File:** utils/helpers/address.go (L143-167)
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

**File:** utils/helpers/associate.go (L57-82)
```go
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
```

**File:** contracts/test/EVMPrecompileTest.js (L70-77)
```javascript
            const message = `Please sign this message to link your EVM and Sei addresses. No SEI will be spent as a result of this signature.\n\n`;
            const messageLength = Buffer.from(message, 'utf8').length;
            const signatureHex = await unassociatedWallet.signMessage(message);

            const sig = hre.ethers.Signature.from(signatureHex);
            
            const appendedMessage = `\x19Ethereum Signed Message:\n${messageLength}${message}`;
            const associatedAddrs = await addr.associate(`0x${sig.v-27}`, sig.r, sig.s, appendedMessage)
```
