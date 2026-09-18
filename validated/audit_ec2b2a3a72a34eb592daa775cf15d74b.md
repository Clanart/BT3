### Title
Reusable off-chain signatures allow forced Sei⇄EVM address association and balance migration - ([File: precompiles/addr/addr.go])

### Summary
The `addr` precompile's `associate()` method authenticates an address-linking request using an ECDSA signature over an arbitrary, attacker-controllable message hash, with no binding to the destination chain, the precompile's own address, a nonce, or an expiry. Any signature a victim has ever produced with `personal_sign`/`eth_sign` (for any purpose, on any chain or dApp) can be replayed by anyone to force a permanent EVM↔Sei address association for that victim, which automatically triggers an unrequested balance migration.

### Finding Description
`associate()` recovers `(evmAddr, seiAddr, pubkey)` purely from `crypto.Keccak256Hash([]byte(customMessage))` and the supplied `(v, r, s)`: [1](#0-0) 

`customMessage` is a free-form string chosen by whoever calls the precompile — it is never validated to equal a canonical "associate" message, nor is it bound to `block.chainid`, the precompile's own address (`0x1004`), a per-account nonce, or an expiry timestamp. Any caller can submit any `(v, r, s, customMessage)` tuple recovered from any signature the target private key has ever produced elsewhere (a personal_sign login for an unrelated dApp, an off-chain order signature, a message signed on a completely different EVM chain, etc.), and the precompile will accept it as proof of intent to associate.

This is the same root cause pattern as the reported bug: an authentication credential (here, a raw ECDSA signature over an unscoped hash) that omits chain ID, destination-contract binding, and expiry can be replayed outside its intended context.

Once a valid signature/hash pair is found, `associateAddresses()` performs a one-time, permanent, irreversible action: [2](#0-1) 

which calls `AssociationHelper.AssociateAddresses`, which unconditionally migrates the entire `SpendableCoins` and wei balance of the address-cast account (`sdk.AccAddress(evmAddr[:])`) to the newly linked bech32 `seiAddr`, and deletes the cast `BaseAccount` if it has no locked coins: [3](#0-2) 

Notably, elsewhere in the codebase (the EIP-7702 `SetCodeAuthorization` pre-association path) the exact same risk is explicitly called out and guarded against with a chain-ID check, because forced/foreign-chain association can "migrate their direct-cast balance and orphaning staking/distribution state": [4](#0-3) 

The `addr` precompile's `associate()` path has no equivalent protection — no chain ID, no contract binding, no nonce/expiry — for what is functionally the same "force someone's cast-address funds to move" hazard.

### Impact Explanation
Anyone who obtains a victim's already-signed message from any prior, unrelated context (any dApp `personal_sign`, any other EVM chain, or even Sei itself) can replay it through `associate()` to unilaterally trigger:
- Involuntary migration of the victim's spendable and wei balances held under the implicit `sdk.AccAddress(evmAddr[:])` cast address to the associated bech32 address.
- Deletion of the cast `BaseAccount` if it has no locked coins, which can disrupt account state (sequence/pubkey) that other flows may depend on.
- Permanent, one-time consumption of the association slot for that EVM/Sei address pair (subsequent legitimate association attempts revert with "already associated"), effectively locking in an association the victim never authorized for Sei specifically.

This is a fund-moving side effect (`bankKeeper.SendCoins`/`SendCoinsAndWei`) triggered by unauthorized replay of a credential the victim generated for a different purpose or chain — matching the "unauthorized transfer via precompile" and "Sei⇄EVM address association" categories called out as in-scope.

### Likelihood Explanation
`personal_sign`/`eth_sign` signatures are commonly produced by wallets for logins, off-chain attestations, and other dApps, and are frequently visible on-chain or off-chain (e.g., posted as proof, leaked via a compromised frontend, or captured from a wallet-connect flow). No special privilege is needed to call the public precompile method — any address can submit the replayed tuple in a normal EVM transaction to `0x0000000000000000000000000000000000001004`.

### Recommendation
Bind the association signature to a Sei-specific, single-use, chain-scoped domain: include `block.chainid`, the precompile address, and either a strictly-increasing nonce or an expiry timestamp in the hashed payload (e.g., via EIP-712 typed-data domain separation), and reject any `customMessage` that does not match the canonical, precompile-generated template. This mirrors the fix already applied in `AuthorityToPreAssociate` for the EIP-7702 authorization path.

### Proof of Concept
1. Victim signs any `personal_sign` message `M` for an unrelated purpose (e.g., "Sign in to ExampleApp") with their EVM key, producing signature `(v, r, s)`. This signature is often visible in application logs, browser storage, or a public transaction/API payload.
2. Attacker computes the EIP-191-prefixed bytes of `M` and calls `addr.associate(v-27, r, s, prefixed(M))` on Sei, exactly as shown in the project's own test helper: [5](#0-4) 
3. `associate()` recovers the victim's `(evmAddr, seiAddr, pubkey)` from `Keccak256Hash(prefixed(M))` and `(v, r, s)`, and `associateAddresses()` executes, permanently linking the accounts and migrating any `SpendableCoins`/wei balance sitting under `sdk.AccAddress(evmAddr[:])` to the derived Sei address — without the victim ever intending to perform a Sei-specific association.

### Citations

**File:** precompiles/addr/addr.go (L169-202)
```go
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

**File:** utils/helpers/address.go (L146-188)
```go
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

**File:** contracts/test/EVMPrecompileTest.js (L70-78)
```javascript
            const message = `Please sign this message to link your EVM and Sei addresses. No SEI will be spent as a result of this signature.\n\n`;
            const messageLength = Buffer.from(message, 'utf8').length;
            const signatureHex = await unassociatedWallet.signMessage(message);

            const sig = hre.ethers.Signature.from(signatureHex);
            
            const appendedMessage = `\x19Ethereum Signed Message:\n${messageLength}${message}`;
            const associatedAddrs = await addr.associate(`0x${sig.v-27}`, sig.r, sig.s, appendedMessage)
            const addrs = await associatedAddrs.wait();
```
