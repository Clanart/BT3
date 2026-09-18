### Title
`associatePublicKey` allows force-associating any address without a signature - ([File: precompiles/addr/addr.go])

### Summary
The `Addr` precompile's `associatePublicKey` method (`Associate` = "associatePubKey") lets any caller submit a raw secp256k1 public key and permanently link the derived Sei and EVM addresses, without any proof that the caller (or the key owner) authorized this specific action. This is analogous to the reported Plone CSRF class: a state-changing action ("rename"/"associate") is performed on behalf of a third party using material the attacker did not need special privilege to obtain (a valid but unrelated public key, freely observable on-chain from any prior Cosmos transaction) and no possession/intent proof tied to that specific call.

### Finding Description
`associate` (the sibling method) requires a fresh ECDSA signature (`v,r,s`) over a caller-chosen `customMessage`, which proves the caller currently holds the private key and is intentionally requesting an association [1](#0-0) .

`associatePublicKey`, however, takes only a hex-encoded compressed public key, parses it, derives `evmAddr`/`seiAddr` directly from it, and immediately calls `associateAddresses` — there is no signature, nonce, or message check at all: [2](#0-1) 

`associateAddresses` only checks that the target Sei address is not *already* associated before performing the association and (per the migration commentary in `utils/helpers/address.go`) can migrate the account's direct-cast balance and orphan staking/distribution state: [3](#0-2) [4](#0-3) 

Because Cosmos SDK transactions embed the sender's full public key in every signed message, any account's compressed public key is trivially harvestable from chain history by an unprivileged observer. An attacker can therefore call `associatePubKey(victimPubKeyHex)` via a plain EVM transaction to force an association for a victim who never opted in, never signed anything for this purpose, and may not even use the EVM side of Sei.

This mirrors the CSRF bug class in the report: a sensitive, one-way state mutation (`renameObjectsByPaths` in Plone / `associatePublicKey` here) is triggered using externally-visible material (a valid CSRF token / a valid public key) with no verification that the actual intended party requested that specific action.

### Impact Explanation
Address association is a one-time, effectively irreversible binding (guarded only by "not already associated") that migrates the account's direct-cast usei balance to the EVM-addressable model and can affect staking/distribution bookkeeping, as explicitly called out in the codebase's own security comment about association migration risk [4](#0-3) . Forcing this migration onto a victim without consent can disrupt their expected balance/staking behavior and pre-empts the victim's own ability to legitimately associate later (since `associateAddresses` rejects already-associated addresses) [5](#0-4) . This is a state-integrity violation reachable by any unprivileged EVM transaction sender against any Sei/Cosmos account on chain, satisfying the "unauthorized transfer/migration" and permanent-state-change impact bar.

### Likelihood Explanation
Likelihood is high: the public key of any active account is public information (present in every signed Cosmos tx), the precompile is reachable via a standard EVM `eth_sendRawTransaction`/`eth_call` at address `0x0000000000000000000000000000000000001004`, and no rate limiting, fee-based deterrent beyond normal gas, or authorization check exists beyond "not yet associated" [6](#0-5) .

### Recommendation
Require the same proof-of-possession as `associate` for `associatePublicKey` (e.g., require a fresh signature over a domain-separated message from the presented public key), or remove/gate the no-signature path entirely so that on-chain association can only be triggered by the key owner (or by a Cosmos-side tx actually signed by that key), never by a third party merely relaying a previously observed public key.

### Proof of Concept
1. Observe any Sei account's public key from a previously broadcast Cosmos transaction (public keys are embedded in tx signatures and indexed by explorers/RPC).
2. From an attacker-controlled EVM account, call the `Addr` precompile's `associatePubKey(bytes pubKey)` method (selector for `AssociatePubKey`) at `0x0000000000000000000000000000000000001004`, passing the victim's compressed public key hex.
3. `associatePublicKey` parses the key, derives `evmAddr`/`seiAddr` [7](#0-6) , and `associateAddresses` performs the binding as long as the victim wasn't already associated [3](#0-2)  — completing the forced association with no signature or consent from the victim.

### Citations

**File:** precompiles/addr/addr.go (L39-41)
```go
const (
	AddrAddress = "0x0000000000000000000000000000000000001004"
)
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

**File:** utils/helpers/address.go (L149-155)
```go
// already associated. Mirroring validateAuthorization is essential to security: the
// authorization sig hash is computed from the auth's own ChainID, so recovery and
// auth.Authority() succeed for an authorization signed for ANY chain. Without these checks a
// publicly-visible authorization a user signed for another chain (e.g. Ethereum mainnet)
// could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their
// direct-cast balance and orphaning staking/distribution state — even though the EVM skips
// the wrong-chain authorization and installs no delegation.
```
