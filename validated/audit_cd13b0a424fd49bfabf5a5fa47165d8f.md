### Title
`addr` precompile's `associate()` accepts any raw ECDSA signature without binding it to an association-specific domain, enabling forced account association and balance migration - (`precompiles/addr/addr.go`)

### Summary
The CVE-2024-29038 bug class is: a verification routine accepts externally-supplied cryptographic proof data without checking that it was actually produced for the specific purpose/context being verified, so a signature/attestation intended for something else can be replayed and accepted as valid input for a security-sensitive action. The `associate` method of Sei's `addr` precompile has the same root cause: it recovers a pubkey from a caller-supplied `(v, r, s, customMessage)` tuple with **no domain separation prefix and no requirement that the message actually be an "association" message**, then unconditionally uses whatever key it recovers to force an address association and migrate funds.

### Finding Description
`PrecompileExecutor.associate` in `precompiles/addr/addr.go` takes attacker-controlled `v`, `r`, `s`, and `customMessage`, hashes `customMessage` with raw `keccak256` (no `"\x19Ethereum Signed Message:\n"` prefix or protocol-specific tag), and recovers the signer's public key from that hash via `helpers.GetAddresses`/`RecoverPubkey`: [1](#0-0) 

`RecoverPubkey` is a bare `crypto.Ecrecover` call with no context-binding check that the signature was ever meant to authorize an "association": [2](#0-1) 

The recovered address pair is then fed straight into `associateAddresses`, which calls `AssociationHelper.AssociateAddresses`. This helper unconditionally sets the address mapping, assigns the pubkey to the account, and migrates all spendable coins/wei from the EVM-address-derived "cast" account to the Sei address, then removes the cast account: [3](#0-2) [4](#0-3) 

The only guard is that the target Sei address must not already be associated: [5](#0-4) 

Because the `customMessageHash` has no fixed, protocol-specific prefix/tag distinguishing it from any other message a user might sign elsewhere (e.g., an off-chain `personal_sign`-style message for an unrelated dApp that also uses a raw/known digest), any third party who obtains such a signature can submit it to `associate()` themselves. The precompile has no way to detect that the signature "was not generated for an association" — mirroring `tpm2 checkquote`'s failure to verify that signed data was actually a quote rather than arbitrary attester-signed data.

### Impact Explanation
Anyone possessing a victim's raw secp256k1 signature over a message whose keccak256 digest they can reproduce as `customMessage` can force that victim's EVM/Sei address association and trigger `MigrateBalance`, which moves all of the victim's `usei`/wei balance from the EVM-address-derived shadow account to the newly bound Sei address and deletes the shadow account. This is an unauthorized, attacker-triggered state change and fund movement performed without the victim's consent for this specific action, and it is irreversible once the association is set (the "already associated" check then permanently blocks any future association attempt by the legitimate owner through their intended flow). This satisfies "unauthorized transfer via precompile" / fund-relocation impact classes even though the ultimate destination address is cryptographically tied to the victim.

### Likelihood Explanation
Reachable directly and permissionlessly by any EVM caller submitting a normal transaction to the `addr` precompile at `0x0000000000000000000000000000000000001004` — no special privileges are required, and no on-chain state prevents reuse of a signature obtained from an unrelated signing context. The only prerequisite is the attacker obtaining any valid `(v,r,s)` over a keccak256 digest they can present as `customMessage`.

### Recommendation
Bind the digest verified in `associate()` to a fixed, protocol-specific domain separator/tag (e.g., `keccak256("SeiAssociate:" + customMessage)` or an EIP-191/EIP-712 typed-data prefix unique to this precompile) so that a signature produced for any other purpose cannot be replayed here. Additionally consider requiring the transaction sender to match the recovered EVM address, or gating balance migration behind the same funded-account checks already used in `x/evm/ante/preprocess.go`'s `isAssociateTx` path.

### Proof of Concept
1. Victim signs an arbitrary raw message `M` (using `eth_sign` or a wallet that does not apply the EIP-191 prefix) for some unrelated off-chain purpose, producing `(v, r, s)` over `keccak256(M)`.
2. Attacker observes/obtains this `(v, r, s)` and calls `associate(v, r, s, M)` on the `addr` precompile as `precompiles/addr/addr.go`'s `associate` function.
3. `helpers.GetAddresses` recovers the victim's real `evmAddr`/`seiAddr`/pubkey since `keccak256(M)` matches what the victim actually signed.
4. `associateAddresses` → `AssociateAddresses` executes, permanently binding the victim's addresses and calling `MigrateBalance`, which force-moves the victim's `usei`/wei balance out of the EVM-address-derived shadow account — all without the victim ever intending to call `associate`.

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

**File:** precompiles/addr/addr.go (L239-245)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}

```

**File:** utils/helpers/legacy/v600/address.go (L35-53)
```go
// first half of go-ethereum/core/types/transaction_signing.go:recoverPlain
func RecoverPubkey(sighash common.Hash, R, S, Vb *big.Int, homestead bool) ([]byte, error) {
	if Vb.BitLen() > 8 {
		return []byte{}, ethtypes.ErrInvalidSig
	}
	V := byte(Vb.Uint64() - 27)
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

**File:** utils/helpers/associate.go (L57-83)
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
}
```
