## Title
Signature replay via `associate` precompile force-associates a victim's EVM address without their consent, due to missing domain separation on the signed message - (File: precompiles/addr/addr.go)

### Summary
The `addr` precompile's `associate` method recovers an ECDSA public key from a caller-supplied `(v, r, s, customMessage)` tuple and unconditionally binds the recovered EVM/Sei address pair, with no requirement that `customMessage` be scoped to this specific action, this chain, or this precompile. Any valid secp256k1 signature the victim has ever produced over an attacker-known message can be replayed here to force an unwanted, irreversible address association.

### Finding Description
`PrecompileExecutor.associate` in `precompiles/addr/addr.go` takes `v`, `r`, `s`, and an arbitrary `customMessage` string, hashes the message with `keccak256`, and recovers a pubkey purely from the signature over that hash: [1](#0-0) 

`helpers.GetAddresses` / `RecoverPubkey` just perform `crypto.Ecrecover` on whatever hash is passed in - there is no binding to chain ID, the precompile's own address, a purpose string, a nonce, or any other domain separator: [2](#0-1) [3](#0-2) 

The resulting `evmAddr`/`seiAddr` are derived entirely from whichever key produced the signature - not chosen by the caller - and `associateAddresses` persists the mapping as long as the target `seiAddr` isn't already associated: [4](#0-3) 

This is structurally the same root cause as the referenced report: verification checks that a signature/proof is *cryptographically valid* but fails to check that it was produced *for this specific purpose and context*. In the original report, an incident/merkle proof lacked binding to a specific insurance policy and time window, letting a proof crafted for one purpose be reused for another. Here, the signed message has no binding to "I intend to associate my Sei/EVM address on this chain via this precompile" - any signature the victim ever created over a message an attacker can observe (e.g., a personal-sign login challenge, an off-chain agreement, a signature leaked from another dApp or another EVM chain) hashes to the same `customMessageHash` and will pass `Ecrecover`, letting the attacker submit it to `associate()` unmodified.

Notably, sibling code elsewhere in the same package explicitly documents this exact class of risk for EIP-7702 authorizations and adds chain-ID/nonce/authority checks specifically to prevent "a publicly-visible authorization a user signed for another chain... could be replayed... to force-associate them": [5](#0-4) 

No equivalent protection (chain ID binding, canonical/fixed message format, precompile-address binding, or nonce) exists for the `associate` path in `addr.go`.

### Impact Explanation
Address association in Sei is a foundational, effectively one-way, security-relevant state transition: once `seiAddr` is associated with an `evmAddr`, `associateAddresses` rejects future association attempts for that `seiAddr`: [6](#0-5) 

An attacker who obtains any single signature the victim produced for an unrelated purpose (common in practice - wallet "sign-in" messages, off-chain order signatures, signatures from other EVM chains, etc.) can:
- Force-associate the victim's EVM address with their Sei counterpart on the victim's behalf and timing, without consent, and
- Because association is a one-time/permanent binding, potentially pre-empt or interfere with the victim's own subsequent intentional association, and
- Trigger `AssociateAddresses`, which (per the EIP-7702 comment in the same file) can migrate direct-cast balances and account state tied to the address - i.e., unauthorized manipulation of address-linked fund/state routing reachable purely from a public RPC `eth_call`/tx to the `addr` precompile at `0x0000000000000000000000000000000000001004`.

This satisfies "unauthorized transfer via precompile" / permanent-freezing criteria: the victim's address association can be forced into an unintended, hard-to-reverse state by a third party using only a public signature the victim generated for some other purpose.

### Likelihood Explanation
High-to-medium likelihood: any Sei/EVM key holder who has ever produced a `(message, v, r, s)` tuple visible to an attacker (a common occurrence: personal_sign login flows, ERC-2612 style off-chain signature schemes, cross-chain reuse of the same secp256k1 key) supplies everything an attacker needs. No special privileges, contract deployment, or validator access is required - only a single public transaction/call to the `associate` method of the `addr` precompile, which is reachable by any EVM caller.

### Recommendation
Enforce domain separation on the `associate` signature so it cannot be satisfied by a signature produced for any other purpose:
- Require `customMessage` to match (or be prefixed/suffixed with) a canonical, chain-specific string that embeds the Sei chain ID and the `addr` precompile address (e.g., `"Sei-Associate:{chainId}:{addr precompile address}:{nonce}"`), and validate this format inside `associate` before hashing/recovering, rather than accepting attacker-supplied free text.
- Alternatively/additionally, require a nonce bound to the target Sei/EVM address pair (mirroring the nonce/chain-ID checks already implemented for EIP-7702 authorizations in `AuthorityToPreAssociate`) so a given signature can only ever be consumed once for this specific purpose.
- Apply the same fix pattern used for `AuthorityToPreAssociate` (chain ID check, nonce/replay check, "already associated" check performed atomically with recovery) to the `associate` precompile path.

### Proof of Concept
1. Victim signs an arbitrary message `M` with their secp256k1 key for an unrelated purpose (e.g., a login challenge on some other dApp, or an authorization on another EVM chain), producing `(v, r, s)`.
2. Attacker observes `(M, v, r, s)` publicly (e.g., from that other dApp's request log, from another chain's public tx data, etc.).
3. Attacker calls the Sei `addr` precompile's `associate(v, r, s, M)` at `0x0000000000000000000000000000000000001004`: [7](#0-6) 
4. `helpers.GetAddresses` recovers the victim's pubkey purely from `Ecrecover(keccak256(M), sig)`, with no chain-ID, precompile, or purpose binding: [8](#0-7) 
5. `associateAddresses` persists the mapping (assuming the victim's `seiAddr` wasn't already associated), completing an association the victim never intended to perform via this precompile at this time: [4](#0-3)

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
