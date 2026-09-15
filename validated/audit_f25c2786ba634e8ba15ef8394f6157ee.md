### Title
Signature Replay via `VALIDATE_SENDER` Precompile Due to Missing Domain Separation - (File: blockchain/vm/contracts.go)

### Summary
The `VALIDATE_SENDER` precompiled contract (`validateSender.Run` / `validateSender.validateSender`) lets any calling smart contract pass an arbitrary 32-byte message hash together with an address and a set of 65-byte ECDSA signatures, and returns whether the signatures satisfy that address's `AccountKey` threshold. Exactly like the reported `ERC1271Handler.isValidSignature` bug, the precompile performs no binding of the signed hash to the chain ID, the calling (verifying) contract, or any nonce/purpose tag. Any contract built on top of this precompile to authenticate an account (the documented purpose of `VALIDATE_SENDER`) inherits a signature-replay vulnerability: a signature produced for one purpose/chain/contract can be reused to authenticate as the same address in a completely unrelated contract or chain.

### Finding Description
`validateSender.validateSender` parses `from` (20 bytes), `msg` (32 bytes) and N 65-byte signatures directly from precompile input, `Ecrecover`s each signature against the raw `msg` bytes, and calls `accountkey.ValidateAccountKey` to check that the recovered keys satisfy the account's registered key (single key, weighted multisig, or role-based key for `RoleTransaction`): [1](#0-0) 

No part of `msg` is required to include the chain ID, the calling contract's address, a nonce, or any other domain-separation tag — the precompile blindly trusts whatever 32-byte value the caller passes as `msg` and whatever raw signature bytes are supplied: [2](#0-1) 

This mirrors the external report's root cause precisely: `isValidSignature`/`ValidateSender`-style verification functions that check "does signature S over hash H recover to a key belonging to account A" without constraining H to a specific chain/verifier/purpose are inherently replayable across any two consumers of that check. The precompile is a general-purpose, cross-fork system contract (available at `0x0b`/`0x3ff` depending on hard fork) intended precisely for use by application contracts to authenticate an account holder's signature, as documented in `blockchain/vm/precompiles.go`: [3](#0-2) 
and registered across all fork-specific precompile maps: [4](#0-3) [5](#0-4) 

The existing test/reference implementation (`ValidateSenderContract`) shows the intended integration pattern — an application contract forwards a `sender`, `msgHash`, and `sigs` blob straight to the precompile with no additional domain-separation of its own: [6](#0-5) 

Because `msg` is fully attacker/caller-controlled and unconstrained, any hash the account owner has ever signed for any purpose (a message for a different dApp, a message intended for a different chain, or any other 32-byte value bearing a valid signature from the account's registered key) can be resupplied through any contract that relies on `VALIDATE_SENDER` for authorization, causing that contract to believe the real account authorized the current action.

### Impact Explanation
Any application contract built on the `VALIDATE_SENDER` precompile to gate privileged actions (e.g., signature-based withdrawal authorization, gasless/relayed action authorization, off-chain multisig approval schemes) can be defeated by replaying a previously produced, unrelated signature from the target account. This allows an unprivileged transaction sender who deploys or calls into such a contract to impersonate another account's authorization and trigger unauthorized state changes/value movement, without the victim's current consent, since the precompile provides no protection against cross-contract or cross-chain signature reuse — matching the "unauthorized value movement" impact bar for Medium/High severity.

### Likelihood Explanation
Reachable by any transaction sender or contract deployer in a single call: deploy (or use an existing) contract that calls the `VALIDATE_SENDER` precompile at `0x0b`/`0x3ff` and pass a signature previously produced by the victim for a different purpose/contract/chain. No privileged role, validator collusion, or off-chain component is required — only a legitimately-signed message from the victim that was disclosed or observed elsewhere (e.g., leaked from another dApp, chain, or off-chain flow), which is a realistic and commonly-targeted attack surface for signature-based authorization systems.

### Recommendation
Document (and, where feasible, enforce) that `msg` passed into `VALIDATE_SENDER` must be a domain-separated hash — e.g., require callers to construct the hash as `keccak256(chainId, address(this), nonce/purpose, payload)` before invoking the precompile, and update `blockchain/vm/contracts_test.go`/integration examples (e.g. `contracts/testing/validatesender/validate_sender.sol`) to demonstrate this pattern so downstream contract authors don't reproduce the unbound-hash pattern shown in the current reference implementation. Consider adding an explicit warning in the precompile's documentation (`blockchain/vm/precompiles.go`) about the lack of built-in domain separation, analogous to the ERC-7739 defensive rehashing scheme recommended for `ERC1271Handler`.

### Proof of Concept
1. Victim account `A` (protected by `AccountKeyPublic`/`AccountKeyWeightedMultiSig`) signs message hash `H` for legitimate purpose P in application X (or on a different chain sharing the same key).
2. Attacker deploys/uses application contract Y whose authorization logic calls the `VALIDATE_SENDER` precompile with `from = A`, `msg = H`, `sigs = <A's signature over H>`.
3. `validateSender.validateSender` (blockchain/vm/contracts.go:879-923) recovers the public key from `sigs` over the raw `msg = H` and calls `accountkey.ValidateAccountKey`, which succeeds because the signature is cryptographically valid for `A`'s key — even though `H` was never intended to authorize anything in contract Y.
4. Contract Y treats this as proof that `A` authorized the current action in Y and executes the privileged operation (e.g., value transfer, withdrawal, permission grant) without `A`'s actual consent for that context.

### Citations

**File:** blockchain/vm/contracts.go (L879-923)
```go
func (c *validateSender) validateSender(input []byte, picker types.AccountKeyPicker, currentBlockNumber uint64) error {
	ptr := input

	// Parse the first 20 bytes. They represent an address to be verified.
	if len(ptr) < common.AddressLength {
		return errInputTooShort
	}
	from := common.BytesToAddress(input[0:common.AddressLength])
	ptr = ptr[common.AddressLength:]

	// Parse the next 32 bytes. They represent a message which was used to generate signatures.
	if len(ptr) < common.HashLength {
		return errInputTooShort
	}
	msg := ptr[0:common.HashLength]
	ptr = ptr[common.HashLength:]

	// Parse remaining bytes. The length should be divided by common.SignatureLength.
	if len(ptr)%common.SignatureLength != 0 {
		return errWrongSignatureLength
	}

	numSigs := len(ptr) / common.SignatureLength
	if numSigs == 0 {
		return errNoSignatures
	}
	pubs := make([]*ecdsa.PublicKey, numSigs)
	for i := range numSigs {
		p, err := crypto.Ecrecover(msg, ptr[0:common.SignatureLength])
		if err != nil {
			return err
		}
		pubs[i], err = crypto.UnmarshalPubkey(p)
		if err != nil {
			return err
		}
		ptr = ptr[common.SignatureLength:]
	}

	k := picker.GetKey(from)
	if err := accountkey.ValidateAccountKey(currentBlockNumber, from, k, pubs, accountkey.RoleTransaction); err != nil {
		return err
	}

	return nil
```

**File:** blockchain/vm/precompiles.go (L35-41)
```go
//  3. EVM.GetPrecompiledContractMap / getPrecompiledContractForVersion (this file): caller-vmVersion-specific; for VM execution
//     Returns the exact map used during EVM execution. The map is keyed on
//     the caller's address, not the precompile's: if the caller was deployed
//     before Istanbul (VmVersion0), it always receives the Byzantium map so
//     that its references to 0x09–0x0b (vmLog, feePayer, validateSender) remain
//     valid. Post-Istanbul callers get the fork-appropriate map where 0x09 is
//     blake2F and those three contracts live at 0x3fd–0x3ff.
```

**File:** blockchain/vm/precompiles.go (L54-66)
```go
var PrecompiledContractsByzantium = map[common.Address]PrecompiledContract{
	common.BytesToAddress([]byte{1}):  &ecrecover{},
	common.BytesToAddress([]byte{2}):  &sha256hash{},
	common.BytesToAddress([]byte{3}):  &ripemd160hash{},
	common.BytesToAddress([]byte{4}):  &dataCopy{},
	common.BytesToAddress([]byte{5}):  &bigModExp{eip2565: false, eip7823: false, eip7883: false},
	common.BytesToAddress([]byte{6}):  &bn256AddByzantium{},
	common.BytesToAddress([]byte{7}):  &bn256ScalarMulByzantium{},
	common.BytesToAddress([]byte{8}):  &bn256PairingByzantium{},
	common.BytesToAddress([]byte{9}):  &vmLog{},
	common.BytesToAddress([]byte{10}): &feePayer{},
	common.BytesToAddress([]byte{11}): &validateSender{},
}
```

**File:** blockchain/vm/precompiles.go (L72-85)
```go
var PrecompiledContractsIstanbul = map[common.Address]PrecompiledContract{
	common.BytesToAddress([]byte{1}):      &ecrecover{},
	common.BytesToAddress([]byte{2}):      &sha256hash{},
	common.BytesToAddress([]byte{3}):      &ripemd160hash{},
	common.BytesToAddress([]byte{4}):      &dataCopy{},
	common.BytesToAddress([]byte{5}):      &bigModExp{eip2565: false, eip7823: false, eip7883: false},
	common.BytesToAddress([]byte{6}):      &bn256AddIstanbul{},
	common.BytesToAddress([]byte{7}):      &bn256ScalarMulIstanbul{},
	common.BytesToAddress([]byte{8}):      &bn256PairingIstanbul{},
	common.BytesToAddress([]byte{9}):      &blake2F{},
	common.BytesToAddress([]byte{3, 253}): &vmLog{},
	common.BytesToAddress([]byte{3, 254}): &feePayer{},
	common.BytesToAddress([]byte{3, 255}): &validateSender{},
}
```

**File:** tests/validate_sender_test.go (L207-223)
```go
	// Check if the validation is successful with valid parameters of multisig.
	{
		msg := crypto.Keccak256Hash([]byte{0x1})
		sigs := make([]byte, 65*2)
		s1, err := crypto.Sign(msg[:], multisig.Keys[0])
		assert.Equal(t, nil, err)
		s2, err := crypto.Sign(msg[:], multisig.Keys[1])
		assert.Equal(t, nil, err)

		copy(sigs[0:65], s1[0:65])
		copy(sigs[65:130], s2[0:65])

		data, err := abii.Pack("ValidateSender", multisig.Addr, msg, sigs)
		if err != nil {
			t.Fatal(err)
		}

```
