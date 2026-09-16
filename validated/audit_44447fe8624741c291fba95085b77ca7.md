This confirms the analog. The `validateSender` precompile at address `0x3ff` (formerly `0xb`) is a generic, publicly-callable EVM primitive: any smart contract, reachable by any unprivileged transaction sender, can invoke it with `(address, msgHash, sigs)` where `msgHash` is an arbitrary 32-byte value supplied entirely by the calling contract with **no domain separation** (no contract address, no chain ID, no purpose tag) baked in by the precompile itself.### Title
`validateSender` precompile signature verification lacks domain separation, enabling cross-contract signature replay for AccountKey-authorized actions - (File: `blockchain/vm/contracts.go`)

### Summary
The `validateSender` precompile (address `0x3ff`, historically `0x0b`) lets **any contract, reachable by any unprivileged transaction sender**, ask "was this 32-byte `msg` signed by the `RoleTransaction` key(s) of account `from`?" [1](#0-0)  Unlike the report's `NFTStaking._stakeNFTs()` hash, the precompile itself never mixes in the calling contract's address, the target contract, or any other domain tag into `msg` — the 32-byte hash is taken verbatim from calldata and fed straight to `crypto.Ecrecover` [2](#0-1) . This is the exact same root-cause pattern as the external report (`keccak256(abi.encode(_sender, _tokenIds, _rarityWeightIndexes))` with no `address(this)`): a signature meant for one purpose/contract can be validated as authoritative for a completely different purpose/contract, because nothing in the verified hash binds it to a specific verifying contract.

### Finding Description
`validateSender.validateSender()` parses `[20-byte from][32-byte msg][N*65-byte sigs]`, recovers public keys via `crypto.Ecrecover(msg, sig)` for each signature, and checks them against `from`'s on-chain `AccountKey` for `RoleTransaction` via `accountkey.ValidateAccountKey` [3](#0-2) . Nothing in this code path incorporates `contract.Address()` (the calling contract), `evm.ChainConfig().ChainID`, or a purpose/action tag into `msg` — domain separation is entirely delegated to whichever Solidity contract constructs `msg` before calling the precompile, as illustrated by the sample usage contract that simply forwards caller-supplied `msgHash` unmodified [4](#0-3) .

Because the precompile is a shared, chain-wide primitive at a single well-known address, any two independently-deployed contracts that gate an authorized action (e.g., "did the owner of account X approve this withdrawal/action?") using the same hash-construction convention (e.g., `keccak256(abi.encode(amount, nonce))`, mirroring the NFTStaking pattern of omitting the verifying contract) are cross-replayable: a signature an account owner produced to authorize an action in Contract A validates identically when replayed against Contract B, since `validateSender` only checks `(from, msg, sigs)` and never learns which contract is asking.

### Impact Explanation
If a signature scheme reused across two AccountKey-gated smart contracts on Kaia (both calling `0x3ff`/`validateSender`) omits the verifying contract's address from the signed hash — the same class of omission flagged in the original report — an attacker who obtains or observes a valid `(msg, sig)` pair authorized for one contract can resubmit it to the other contract to unlock privileged actions there (e.g., unauthorized fund release, replay of an approval, or authorization bypass), all reachable purely through a public transaction/contract call, satisfying "unauthorized value movement" / authorization bypass.

### Likelihood Explanation
Medium: exploitation requires a downstream contract author to construct `msg` without domain separation (the same class of oversight the original report demonstrates is realistic and has occurred in production Solidity code), and requires at least one other same-account-key-gated contract using an equivalent hash scheme. The precompile design does nothing to prevent or warn against this, so it is a systemic footgun for any dApp built on `validateSender` rather than a one-off bug, mirroring exactly the conditions that made the NFTStaking cross-contract replay exploitable.

### Recommendation
- Document/require that callers of `validateSender` MUST include `msg.sender` (the calling contract's own address) and `block.chainid` in the hash they sign, e.g. `keccak256(abi.encode(address(this), chainid, ...))`, before invoking the precompile — mirroring the report's fix of adding `address(this)` to the signed payload.
- Consider hardening the precompile itself to accept and hash-bind the immediate caller's address (`contract.CallerAddress` / `contract.Address()`) into the message it actually verifies, rather than trusting the raw caller-supplied 32 bytes verbatim, closing the footgun at the primitive level instead of relying on every downstream contract author to remember domain separation.

### Proof of Concept
1. Contract A (e.g. a subscription/withdrawal contract) authorizes a withdrawal for account `X` if `validateSender(X, keccak256(abi.encode(amount, nonce)), sig)` returns `1`.
2. Contract B (an unrelated contract, deployed by anyone) independently implements the same "generic authorization" pattern using the identical encoding `keccak256(abi.encode(amount, nonce))` (no `address(this)`), because this is the naive/obvious way to use the precompile shown in the reference `ValidateSenderContract` sample [4](#0-3) .
3. Account `X`'s owner signs `msg = keccak256(abi.encode(amount, nonce))` to authorize a withdrawal in Contract A.
4. An attacker (or malicious observer) captures this `(msg, sig)` pair from Contract A's transaction calldata/events and resubmits it to Contract B with the same `amount`/`nonce`.
5. Contract B calls the same `0x3ff` precompile with `(X, msg, sig)`; `validateSender` recovers the same public key and validates it against `X`'s `AccountKey` for `RoleTransaction` [5](#0-4) , returning success — authorizing the action in Contract B that `X` never intended to approve there.

**Uncertainty**: I could not find within the indexed codebase a concrete, currently-deployed production system contract (beyond the testing sample) that consumes `validateSender` with an under-specified hash, so this finding demonstrates a systemic design/API-misuse risk in the shared precompile rather than a confirmed exploit against a specific named production contract. A full audit would need to enumerate all system/production contracts that call `0x3ff` to confirm whether any two of them share a colliding hash-construction convention today.

### Citations

**File:** blockchain/vm/contracts.go (L879-921)
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
```

**File:** contracts/testing/validatesender/validate_sender.sol (L19-34)
```text
contract ValidateSenderContract {

    function ValidateSender(address sender, bytes32 msgHash, bytes sigs) public returns (bool) {
        require(sigs.length % 65 == 0);
        bytes memory data = new bytes(20+32+sigs.length);
        uint idx = 0;
        uint i;
        for( i = 0; i < 20; i++) {
            data[idx++] = (bytes20)(sender)[i];
        }
        for( i = 0; i < 32; i++ ) {
            data[idx++] = msgHash[i];
        }
        for( i = 0; i < sigs.length; i++) {
            data[idx++] = sigs[i];
        }
```
