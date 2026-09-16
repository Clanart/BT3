### Title
`VALIDATE_SENDER` precompile validates raw, caller-unbound message hashes, enabling cross-contract/cross-purpose signature replay analogous to the OnChainLab ERC-1271 raw-hash flaw - (File: blockchain/vm/contracts.go)

### Summary
Kaia's `VALIDATE_SENDER` precompiled contract (`validateSender` in `blockchain/vm/contracts.go`) recovers public keys from an attacker-supplied raw 32-byte `msg` and an arbitrary set of signatures, then checks membership against an account's registered `AccountKey`, exactly like the vulnerable `OnChainLab::isValidSignature` fallback that called `SignatureChecker.isValidSignatureNow(owner(), hash, signature)` over a raw hash with no domain binding.

### Finding Description
The precompile's `validateSender` function parses `from` (20 bytes), `msg` (32 bytes), and one or more 65-byte signatures directly from calldata, then does: [1](#0-0) 
```
k := picker.GetKey(from)
if err := accountkey.ValidateAccountKey(currentBlockNumber, from, k, pubs, accountkey.RoleTransaction); err != nil { ... }
```
`msg` is treated as a raw, opaque hash that is `ecrecover`-ed directly, with **no binding to the calling contract address, `chainid`, the precompile's own domain, or any nonce/purpose tag**. This is structurally identical to the bug class described in the external report: `SignatureChecker.isValidSignatureNow(owner(), hash, signature)` validated a raw `hash` without wrapping it in `keccak256(abi.encode(block.chainid, address(this), hash))`.

Because Kaia transactions and other on-chain signed artifacts are public, any 65-byte `(r,s,v)` signature ever produced by an account's registered key — including signatures whose 32-byte digest happens to equal some other value an unrelated caller-contract wants verified — can be fed into this precompile from **any calling contract**, and the precompile will report success as long as the recovered key matches the account's `AccountKey` for `RoleTransaction`. Any downstream Solidity contract that uses `VALIDATE_SENDER` as a "prove you control address X" authorization primitive (a documented, sanctioned usage pattern per Kaia's own precompile docs) inherits this replay weakness: a signature produced for one purpose/consumer (one contract's domain) is equally valid for a different consuming contract, because the precompile — like the OnChainLab fallback — never binds the raw hash to `address(this)`/`msg.sender` calling context or to the specific application-level domain.

### Impact Explanation
Any contract that relies on `VALIDATE_SENDER` to gate value-moving actions (e.g., "recover funds if you can prove control of the source account key") is exposed to cross-contract/cross-purpose signature replay: an attacker who has observed *any* valid signature by the target account (including ordinary, publicly-visible transaction signatures whose digest coincidentally or intentionally matches a value the attacker controls as `msg`) can reuse it to satisfy authorization checks in an unrelated consuming contract, without ever needing the private key. This can lead to unauthorized authorization/value movement in contracts built atop this system-level primitive, mirroring the "off-chain order books / ERC-1271 consumers... accept the same signature against multiple accounts/domains" impact in the original report.

### Likelihood Explanation
The precompile is directly callable by **any transaction sender** via a plain `CALL`/`STATICCALL` from any contract (or even a raw tx with `to` set to the precompile address), requiring no privileged role — matching the "unprivileged transaction sender" reachability constraint. The only prerequisite is that some consuming DApp use `VALIDATE_SENDER` as an authorization gate over an attacker-influenceable `msg`, which the precompile's own design invites (that is its intended purpose, per `https://docs.kaia.io/docs/learn/computation/precompiled-contracts/`), making exploitation practical wherever this pattern is adopted.

### Recommendation
Do not treat `VALIDATE_SENDER`'s raw `msg` as a trust anchor without additional domain separation at the call-site, or better, have the precompile itself require/mix in `chainid` and the calling contract's address (`contract.CallerAddress`) into the hash it verifies, analogous to the OnChainLab fix of wrapping the hash as `keccak256(abi.encode(block.chainid, address(this), hash))` before recovery. At minimum, update Kaia's precompile documentation to explicitly warn integrators that `VALIDATE_SENDER` performs no domain binding and that callers must include chain-id/contract-address/purpose tags inside the signed `msg` themselves before calling.

### Proof of Concept
Conceptual PoC (Go-level, mirroring the Solidity PoC's structure):
1. Deploy/observe any Kaia account `X` (`AccountKeyPublic`/`AccountKeyLegacy`) that has produced any valid signature `(r,s,v)` over some digest `d` for any purpose (e.g., a normal transaction or an application-specific approval).
2. An unrelated contract `C` (belonging to a different DApp) uses `VALIDATE_SENDER` to check "does `X` authorize action with parameter `d`" by calling the precompile at the `VALIDATE_SENDER` address with `input = X || d || (r,s,v)`, per: [2](#0-1) 
3. Since `validateSender` performs no domain binding, this call succeeds identically to however `X`'s original signature over `d` was validated by its original, unrelated consumer — the same `(d, sig)` pair authorizes actions in contract `C` that `X` never intended to authorize there.
4. Existing repo test `tests/validate_sender_test.go` exercises this precompile end-to-end and can be adapted to demonstrate reuse of the same `(msg, sig)` pair for two different simulated "consuming" call sites/contracts, without any per-contract or per-chain binding failing the check. [3](#0-2) [4](#0-3)

### Citations

**File:** blockchain/vm/contracts.go (L857-877)
```go
type validateSender struct{}

func (c *validateSender) GetRequiredGasAndComputationCost(input []byte) (uint64, uint64) {
	numSigs := uint64(len(input) / common.SignatureLength)
	return numSigs * params.ValidateSenderGas,
		numSigs*params.ValidateSenderPerSigComputationCost + params.ValidateSenderBaseComputationCost
}

func (c *validateSender) Run(input []byte, contract *Contract, evm *EVM) ([]byte, error) {
	if err := c.validateSender(input, evm.StateDB, evm.Context.BlockNumber.Uint64()); err != nil {
		// If return error makes contract execution failed, do not return the error.
		// Instead, print log.
		logger.Trace("validateSender failed", "err", err)
		return []byte{0}, nil
	}
	return []byte{1}, nil
}

func (c *validateSender) Name() string {
	return "VALIDATE_SENDER"
}
```

**File:** blockchain/vm/contracts.go (L879-924)
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
}
```

**File:** blockchain/types/accountkey/account_key.go (L116-121)
```go
func ValidateAccountKey(currentBlockNumber uint64, from common.Address, accKey AccountKey, recoveredKeys []*ecdsa.PublicKey, roleType RoleType) error {
	if !accKey.Validate(currentBlockNumber, roleType, recoveredKeys, from) {
		return errInvalidSignature
	}
	return nil
}
```
