### Title
Untyped/domainless message signing in the `addr` (0x…1004) `associate` precompile enables cross-context signature replay to force premature, unauthorized address association and balance migration - (File: precompiles/addr/addr.go)

### Summary
The `associate` method of the `addr` precompile (`0x0000000000000000000000000000000000001004`) recovers a signer's public key by taking the raw `keccak256` hash of an attacker-supplied `customMessage` string and verifying `(v, r, s)` against it, with no EIP-712-style domain separator, chain ID, contract address, or type hash embedded in the signed payload [1](#0-0) . This is the same root-cause bug class as the Rigor report: because the signed data is a bare, generic string, any pre-existing ECDSA signature over that exact string — obtained from a completely different chain, dApp, or purpose — can be replayed into this function by anyone (the caller does not need to be the signer).

### Finding Description
`associate` takes `v`, `r`, `s`, and an arbitrary `customMessage` string as calldata, computes `customMessageHash := crypto.Keccak256Hash([]byte(customMessage))`, and recovers the public key from that hash via `helpers.GetAddresses` [2](#0-1) [3](#0-2) . The frontend/test convention wraps the message in an EIP-191 envelope (`"\x19Ethereum Signed Message:\n<len><message>"`) before hashing [4](#0-3) , but the precompile itself never validates that `customMessage` equals the canonical association text, contains the caller's address, the chain ID, or any Sei-specific domain tag — it will happily hash and recover from any string. Anyone (not just the key owner) may submit the transaction, since `Execute` performs no `msg.sender`/caller check tying the submitter to the recovered signer [5](#0-4) .

This is functionally identical to the reported vulnerability class: a signature produced for one purpose/one chain/one application can be validated by an unrelated function because the signed payload carries no domain separation. Concretely:
- A signature (v, r, s) over any string a user has ever produced with a `personal_sign`-style flow (EIP-191, keccak256(prefix||message)) elsewhere — on Ethereum mainnet, on a different EVM chain, in a phishing dApp using the identical wire format, or even a previous Sei transaction — can be lifted and resubmitted verbatim as `associate(v, r, s, customMessage)` here.
- Because there is no chain ID or contract-address binding in the hashed payload, the same signature is valid on every chain/deployment that shares this precompile design.
- Because there is no requirement that `customMessage` state anything about "associating a Sei address," a user who signs an innocuous-looking personal message for a different, unrelated dApp can unknowingly have that signature weaponized to trigger `associate` on Sei on their behalf, at a time and block of the attacker's choosing.

The impact of the recovered association is non-trivial: `AssociateAddresses` immediately and irreversibly migrates all spendable balances and wei balance from the EVM-address-derived "cast" account to the newly linked Sei address, and removes the old cast account [6](#0-5) . The maintainers themselves acknowledge in the adjacent EIP-7702 code path that replaying a "publicly-visible" signature intended for another chain to force-associate a victim is a real risk — they added explicit chain-ID and nonce/state checks for the EIP-7702 authorization flow specifically to prevent this class of attack [7](#0-6) [8](#0-7) . The plain `associate` precompile path has no equivalent protection: no chain ID check, no nonce, no domain separator, no restriction on `customMessage` content.

### Impact Explanation
An attacker who obtains any valid signature a victim has produced over an EIP-191-formatted message (from an unrelated app, chain, or even a previously-shared/leaked signature) can force an unwitting, permanent address association and immediate fund migration for that victim's EVM-derived account, at a time of the attacker's choosing rather than the victim's. Because the recovered `evmAddr`/`seiAddr` pair is cryptographically tied to the signer's own pubkey (an attacker cannot redirect funds to their own address), this does not directly let an attacker steal funds to itself, but it does let an attacker: (a) trigger unauthorized, irreversible account migration/merging for a victim who never intended to interact with Sei, deleting the victim's "cast" account and moving its balance [9](#0-8) ; (b) permanently front-run the victim's own intended association (association is a one-time/first-writer-wins operation guarded only by "already associated" checks) [10](#0-9) , and (c) do so using a signature that leaked from a totally unrelated context, satisfying the report's cross-chain/cross-application/phishing replay classes.

### Likelihood Explanation
Likelihood is moderate: it requires an attacker to obtain a victim's `(v, r, s)` over a message that happens to match the EIP-191 preimage format used here (a common signature format for `personal_sign`/`eth_sign`), which can occur via phishing, replay from another chain, or observation of a publicly broadcast signature. No special privilege is needed by the submitter — anyone can call `associate` on-chain for any recovered signer, as the function performs no `msg.sender` binding.

### Recommendation
Adopt EIP-712 typed-data signing for the `associate` flow: define a domain separator that includes the contract address (`0x...1004`) and the chain ID, and a type hash naming the specific association action, rather than hashing an arbitrary caller-supplied string. Additionally, bind the association to a nonce (or the caller's current EVM nonce) so a given signature is single-use, and consider requiring `customMessage` to deterministically embed the target EVM address and chain ID so no cross-account/cross-chain replay is possible, mirroring the chain-ID/nonce checks already implemented for the EIP-7702 authorization pre-association path.

### Proof of Concept
1. Victim (on any EVM chain, or via any dApp using standard `personal_sign`) signs some message `M` via `eth_sign`/`personal_sign`, producing `(v, r, s)` over `keccak256("\x19Ethereum Signed Message:\n" + len(M) + M)`. This signature is visible on-chain or leaked (e.g., posted to a mempool, shared with a third-party service, or captured by a malicious dApp using the identical format).
2. Attacker observes/collects `(v, r, s, M)`.
3. Attacker (using any funded account, not the victim's) calls `associate(v-27, r, s, "\x19Ethereum Signed Message:\n"+len(M)+M)` on the `addr` precompile at `0x0000000000000000000000000000000000001004` on Sei [5](#0-4) .
4. `helpers.GetAddresses` recovers the victim's EVM address, Sei address, and pubkey purely from the hash of `M` and the signature; because there is no domain separator, chain ID, or purpose-binding, the recovery succeeds even though `M` was never signed for Sei association [3](#0-2) .
5. `associateAddresses` executes, immediately calling `AssociateAddresses`, which migrates all spendable and wei balances from the victim's cast account to the newly-associated Sei address and deletes the cast account — all without the victim ever submitting a transaction or intending this action on Sei [11](#0-10) .

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

**File:** utils/helpers/address.go (L44-51)
```go
func GetAddresses(V *big.Int, R *big.Int, S *big.Int, data common.Hash) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	pubkey, err := RecoverPubkey(data, R, S, V, true)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}

	return GetAddressesFromPubkeyBytes(pubkey)
}
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

**File:** contracts/test/EVMPrecompileTest.js (L70-77)
```javascript
            const message = `Please sign this message to link your EVM and Sei addresses. No SEI will be spent as a result of this signature.\n\n`;
            const messageLength = Buffer.from(message, 'utf8').length;
            const signatureHex = await unassociatedWallet.signMessage(message);

            const sig = hre.ethers.Signature.from(signatureHex);
            
            const appendedMessage = `\x19Ethereum Signed Message:\n${messageLength}${message}`;
            const associatedAddrs = await addr.associate(`0x${sig.v-27}`, sig.r, sig.s, appendedMessage)
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

**File:** x/evm/ante/preprocess.go (L133-146)
```go
	for _, auth := range ethTx.SetCodeAuthorizations() {
		// Only pre-associate authorities whose authorization the EVM will actually apply
		// (matching chain id, nonce, and account state). This prevents replaying a
		// publicly-visible authorization a user signed for another chain to force-associate
		// them, which the EVM would skip but which still recovers a valid authority.
		evmAddr, seiAddr, pubkey, ok := helpers.AuthorityToPreAssociate(ctx, p.evmKeeper, auth)
		if !ok {
			continue
		}
		cacheCtx, write := ctx.CacheContext()
		if err := associateHelper.AssociateAddresses(cacheCtx, seiAddr, evmAddr, pubkey, false); err == nil {
			write()
		}
	}
```
