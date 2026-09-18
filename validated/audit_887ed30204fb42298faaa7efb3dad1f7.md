### Title
Signature Replay in `addr` Precompile's `associate()` Enables Forced Address Association Without Domain Separation - (File: precompiles/addr/addr.go)

### Summary
The `addr` precompile's `associate()` function recovers a public key from a caller-supplied `(v, r, s, customMessage)` tuple and, if the resulting Sei address isn't already associated, permanently binds it to the recovered EVM address. The hash that is signature-checked is a bare `keccak256(customMessage)` with no chain ID, contract address, or purpose string mixed in, so any ECDSA signature the victim ever produced for an unrelated purpose (a different dApp's `personal_sign` prompt, a signature on another chain, etc.) can be replayed here to force that association — exactly the missing-domain-separation signature-replay class described in the external `ApprovedCallsPolicy` report.

### Finding Description
`associate()` computes:
```go
customMessageHash := crypto.Keccak256Hash([]byte(customMessage))
evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)
``` [1](#0-0) 

`customMessage` is an entirely caller-supplied string, and the hash fed into ECDSA recovery contains no chain ID, no verifying-contract address, and no fixed protocol/purpose marker — it is whatever raw bytes the caller passes in. This mirrors the report's root cause verbatim: the `ApprovedCallsPolicy` `messageHash` bug was that multiple consumers of the same signature scheme lacked a binding to the verifying contract, enabling cross-context replay.

The codebase's own comments confirm the team is aware this exact bug class is dangerous for address association. For the sibling EIP-7702 authorization path, `RecoverAddressesFromAuthorization`/`AuthorityToPreAssociate` explicitly binds the authorization hash to `ChainID` and documents why: [2](#0-1) 
That comment states that without chain-ID and validity checks, "a publicly-visible authorization a user signed for another chain (e.g. Ethereum mainnet) could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their direct-cast balance and orphaning staking/distribution state." The `addr` precompile's `associate()` path performs the *same category of forced association* but has none of those protections: no chain-ID binding, no EIP-191 personal-sign prefix enforced on-chain, and no restriction on what `customMessage` may contain — it accepts any `(v, r, s, message)` that recovers a valid pubkey.

The integration test harness itself constructs `customMessage` as a full EIP-191 `personal_sign` envelope (`"\x19Ethereum Signed Message:\n<len><message>"`) computed client-side, then passes that whole string on-chain: [3](#0-2) 
This confirms the intended flow is that ordinary wallet `personal_sign` signatures are accepted directly by the contract. Because `personal_sign` signatures are not chain- or dApp-scoped by convention, any signature a victim ever produced under this scheme for a completely different purpose (a "Sign in with Ethereum" nonce, a Terms-of-Service acknowledgment, an off-chain authorization for another protocol) can be captured and replayed verbatim as the `(v, r, s, customMessage)` triple to `associate()`.

### Impact Explanation
An unprivileged attacker who obtains any single `personal_sign`-style ECDSA signature ever produced by a victim (from a phishing prompt, a public block explorer, another dApp's sign-in flow, etc.) can call `associate()` on Sei's `addr` precompile (`0x1004`) to force-bind that victim's true (pubkey-derived) Sei address to their EVM address — without the victim ever intending or consenting to interact with Sei. Per the codebase's own security rationale for the analogous SetCode path, forced association of this kind migrates the victim's direct-cast balance and orphans their staking/distribution state. Because `AssociateAddresses` guards only against re-associating an *already associated* address, not against unauthorized signature origin/purpose, this is directly reachable and causes unauthorized state changes to the victim's account/funds routing.

### Likelihood Explanation
Medium-High. It requires the attacker to possess one authentic ECDSA signature by the target — an easy bar in practice because `personal_sign` prompts are ubiquitous (dApp logins, NFT marketplaces, bridges, airdrop claims) and are not chain/purpose-scoped by convention; users routinely sign arbitrary strings without knowledge that the signature could later be repurposed elsewhere. No special privilege, validator collusion, or governance action is needed — a single public transaction call to the precompile suffices.

### Recommendation
Bind the `associate()` message hash to a Sei-specific, non-reusable domain, following the same approach already used for EIP-7702 authorizations in `AuthorityToPreAssociate`/`RecoverAddressesFromAuthorization` [4](#0-3) :
- Prefix/mix in the chain ID and the `addr` precompile's own address (or a fixed Sei-specific magic string) into the hash instead of hashing the raw caller-supplied `customMessage`.
- Require a fixed, protocol-defined message format (e.g., embed the target Sei/EVM address pair and chain ID) rather than accepting arbitrary attacker-chosen bytes, so a signature produced for any other purpose cannot recover to a hash this precompile accepts.
- Add a nonce or replay marker if `associate()` is intended to remain callable multiple times for different addresses in future upgrades.

### Proof of Concept
1. Attacker observes/obtains any `personal_sign` signature `(v, r, s)` over some message `M` previously produced by victim `V` for an unrelated purpose (e.g., a "Sign in to ExampleDApp" nonce message, captured via a malicious/compromised frontend or from public logs).
2. Attacker reconstructs the exact EIP-191-wrapped byte string that was actually signed, `wrappedM = "\x19Ethereum Signed Message:\n" + len(M) + M`, matching the `eip191Envelope` helper's construction in the test harness. [3](#0-2) 
3. Attacker calls `addr.associate(v, r, s, wrappedM)` on precompile `0x1004`, supplying the exact `v/r/s` from the captured signature and `wrappedM` as `customMessage`.
4. `associate()` computes `customMessageHash = keccak256(wrappedM)` and calls `helpers.GetAddresses(v, r, s, customMessageHash)`, which recovers victim `V`'s public key and derives `V`'s Sei/EVM addresses, all without any check that this signature was ever intended for Sei's `addr` precompile. [5](#0-4) 
5. If `V`'s Sei address is not yet associated, `associateAddresses()` proceeds to bind it, migrating direct-cast balance/state as warned about in the comment for the analogous SetCode path. [6](#0-5) [7](#0-6)

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

**File:** utils/helpers/address.go (L116-132)
```go
// RecoverAddressesFromAuthorization recovers the EVM address, Sei address, and public
// key of the account that signed an EIP-7702 SetCode authorization (the "authority").
// The authorization sig hash is keccak256(0x05 || rlp([chainId, address, nonce])) and
// the recovery id is carried directly in auth.V (yParity, 0 or 1), which GetAddresses
// expects bumped by 27. This mirrors go-ethereum's SetCodeAuthorization.Authority(), but
// additionally returns the recovered public key so the authority can be associated with
// its true Sei address.
func RecoverAddressesFromAuthorization(auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	var buf bytes.Buffer
	buf.WriteByte(eip7702MagicPrefix)
	if err := rlp.Encode(&buf, []any{auth.ChainID, auth.Address, auth.Nonce}); err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	sigHash := crypto.Keccak256Hash(buf.Bytes())
	v := new(big.Int).SetUint64(uint64(auth.V) + 27)
	return GetAddresses(v, auth.R.ToBig(), auth.S.ToBig(), sigHash)
}
```

**File:** utils/helpers/address.go (L143-156)
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
```

**File:** integration_test/precompile_tests/precompiles/addr.spec.ts (L31-38)
```typescript
const eip191Envelope = (message: string): string =>
    `\x19Ethereum Signed Message:\n${Buffer.from(message, 'utf8').length}${message}`;

/** v/r/s in the precompile's expected shape: v is the 0/1 recovery id as hex. */
const signatureParts = async (wallet: EvmAccount, message: string) => {
    const sig = ethers.Signature.from(await wallet.wallet.signMessage(message));
    return { v: `0x${sig.v - 27}`, r: sig.r, s: sig.s };
};
```
