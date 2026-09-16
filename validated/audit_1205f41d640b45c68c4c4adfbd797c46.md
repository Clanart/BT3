### Title
VALIDATE_SENDER precompile accepts signature-malleable ECDSA signatures, enabling replay of raw-signature-keyed authorizations - (File: blockchain/vm/contracts.go)

### Summary
The `VALIDATE_SENDER` precompile (address `0x0b` pre-Istanbul / `0x3ff` post-Istanbul) lets any contract call recover signer public keys from arbitrary (message, signature) pairs and check them against an account's `AccountKey`. Unlike every other signature-recovery path in Kaia, it never calls `crypto.ValidateSignatureValues`, so it accepts both the canonical low-`s` signature and its malleable high-`s`/flipped-`v` counterpart as "valid" for the same signer, mirroring the exact bug class exploited in the TCH incident (a token contract's own signature verification accepted malleable variants of a signature, letting the attacker replay a burn authorization that was supposed to be single-use).

### Finding Description
`validateSender.Run` / `validateSender.validateSender` parses `(from, msg, sig...)` from calldata and recovers a public key per signature using `crypto.Ecrecover` directly: [1](#0-0) 

`crypto.Ecrecover` (both cgo and non-cgo implementations) only validates that the recovery id is in `{0,1}`; it does **not** call `crypto.ValidateSignatureValues`, so it does not reject signatures whose `s` is in the upper half of the curve order: [2](#0-1) [3](#0-2) 

Compare this with every other signer-recovery path in Kaia, all of which explicitly call `crypto.ValidateSignatureValues(v, r, s, true)` to reject the malleable (upper-half-`s`) counterpart of a signature before recovery:
- Transaction sender/fee-payer recovery: [4](#0-3) 
- EIP-7702 `SetCodeAuthorization.Authority()`: [5](#0-4) 
- Auction bid signature recovery (`getSigner`): [6](#0-5) 
- Kaia's own crypto layer explicitly documents this as the malleability defense: [7](#0-6) 

`VALIDATE_SENDER` is the one signature-verification entry point reachable from an ordinary, unprivileged smart-contract call (any deployed contract on Kaia can invoke it) that skips this check. Because for every valid ECDSA signature `(r, s, v)` there exists a second, distinct byte-string signature `(r, N-s, 1-v)` recovering to the identical public key, any application built on top of `VALIDATE_SENDER` that tracks "used" off-chain authorizations by the raw signature bytes (rather than an explicit nonce field) can have that check bypassed exactly as happened in the TCH exploit (`burnToken`'s nonce/signature dedup was bypassed by flipping the last signature byte from `0x1c`→`0x01`).

### Impact Explanation
Any Kaia contract relying on `VALIDATE_SENDER` to authorize a one-time action gated by signature identity (e.g., custom AccountKey-based meta-transaction/authorization schemes, similar to TCH's `burnToken`) inherits the malleability weakness: an attacker who has observed one valid signature for a given AccountKey member can derive a second, bitwise-different but semantically identical signature and force the precompile to accept it again. This can result in double-execution of privileged operations gated purely on "has this account-key holder authorized this message," directly enabling unauthorized repeated state changes/value movement analogous to the TCH double-burn/price-manipulation exploit. This is a Medium-severity issue because it depends on downstream contracts using raw-signature-based replay protection (a known-bad but still common pattern, as evidenced by the original TCH loss), rather than being immediately exploitable purely within kaia-node itself.

### Likelihood Explanation
Likelihood is moderate: the flaw requires a Kaia dApp to (a) use `VALIDATE_SENDER` as its signature verification primitive for AccountKey holders, and (b) implement its own anti-replay logic keyed by raw signature bytes instead of a semantic nonce — exactly the pattern that caused the real-world TCH loss on BNB Chain. Since `VALIDATE_SENDER` is a documented, callable system precompile intended precisely for such AccountKey-based authorization use cases, and no defense-in-depth exists at the precompile level (unlike every other signature-recovery code path in the codebase), the underlying enabling condition is fully present and reachable by any unprivileged transaction/contract deployer today.

### Recommendation
Add the same malleability check used everywhere else in the codebase before calling `crypto.Ecrecover` in `validateSender.validateSender`: call `crypto.ValidateSignatureValues(v, r, s, true)` on the parsed `v`/`r`/`s` for each signature and reject the call (return an error causing `Run` to return `[]byte{0}`) if any signature has an upper-half `s` value, matching the recoverPlainCommon behavior at [4](#0-3) .

### Proof of Concept
1. A contract on Kaia implements a one-time-authorization pattern like TCH's `burnToken(amount, nonce, signature)`, verifying the signer via a call to precompile `0x3ff` (`VALIDATE_SENDER`) with `(from, keccak256(amount,nonce), signature)`, and marks the signature as "used" via `mapping(bytes => bool) usedSigs` keyed on the raw signature bytes (a real-world, previously-exploited pattern).
2. A legitimate authorization signature `sig = (r, s, v)` is signed once by the account-key holder and consumed, setting `usedSigs[sig] = true`.
3. Attacker computes the malleable counterpart `sig' = (r, secp256k1N - s, 1 - (v-27) + 27)` for the exact same `(from, msg)` pair.
4. Attacker calls the contract again with `sig'`. Since `usedSigs[sig']` is `false` (different raw bytes), the replay check passes; the contract calls `VALIDATE_SENDER(0x3ff)` with `sig'`, which internally calls `crypto.Ecrecover` (no `ValidateSignatureValues` malleability check) and recovers the same public key as `sig`, satisfying `AccountKey.Validate`, so the precompile returns `1` (valid) and the privileged action executes a second time — reproducing the TCH double-spend/double-burn pattern natively on Kaia.

### Citations

**File:** blockchain/vm/contracts.go (L905-916)
```go
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
```

**File:** crypto/signature_cgo.go (L36-49)
```go
// Ecrecover returns the uncompressed public key that created the given signature.
func Ecrecover(hash, sig []byte) ([]byte, error) {
	if len(sig) != SignatureLength {
		return nil, errors.New("invalid signature")
	}
	if len(hash) != DigestLength {
		return nil, fmt.Errorf("hash is required to be exactly %d bytes (%d)", DigestLength, len(hash))
	}
	// Enforce canonical Ethereum recovery id v ∈ {0, 1}.
	if sig[RecoveryIDOffset] >= 2 {
		return nil, errors.New("invalid signature recovery id")
	}
	return secp256k1.RecoverPubkey(hash, sig)
}
```

**File:** crypto/signature_nocgo.go (L37-45)
```go
// Ecrecover returns the uncompressed public key that created the given signature.
func Ecrecover(hash, sig []byte) ([]byte, error) {
	pub, err := sigToPub(hash, sig)
	if err != nil {
		return nil, err
	}
	bytes := pub.SerializeUncompressed()
	return bytes, err
}
```

**File:** blockchain/types/transaction_signing.go (L699-706)
```go
func recoverPlainCommon(sighash common.Hash, R, S, Vb *big.Int, homestead bool) ([]byte, error) {
	if Vb.BitLen() > 8 {
		return []byte{}, ErrInvalidSig
	}
	V := byte(Vb.Uint64() - 27)
	if !crypto.ValidateSignatureValues(V, R, S, homestead) {
		return []byte{}, ErrInvalidSig
	}
```

**File:** blockchain/types/tx_internal_data_ethereum_set_code.go (L487-492)
```go
// Authority recovers the authorizing account of an authorization.
func (a *SetCodeAuthorization) Authority() (common.Address, error) {
	sighash := a.sigHash()
	if !crypto.ValidateSignatureValues(a.V, a.R.ToBig(), a.S.ToBig(), true) {
		return common.Address{}, ErrInvalidSig
	}
```

**File:** kaiax/auction/bid.go (L142-154)
```go
func getSigner(sig, digest []byte) (common.Address, error) {
	// Manually convert V from 27/28 to 0/1
	copiedSig := slices.Clone(sig)
	if copiedSig[crypto.RecoveryIDOffset] == 27 || copiedSig[crypto.RecoveryIDOffset] == 28 {
		copiedSig[crypto.RecoveryIDOffset] -= 27
	}

	v := copiedSig[crypto.RecoveryIDOffset]
	r := new(big.Int).SetBytes(copiedSig[0:32])
	s := new(big.Int).SetBytes(copiedSig[32:64])
	if !crypto.ValidateSignatureValues(v, r, s, true) {
		return common.Address{}, ErrInvalidSignature
	}
```

**File:** crypto/crypto.go (L217-230)
```go
// ValidateSignatureValues verifies whether the signature values are valid with
// the given chain rules. The v value is assumed to be either 0 or 1.
func ValidateSignatureValues(v byte, r, s *big.Int, homestead bool) bool {
	if r.Cmp(common.Big1) < 0 || s.Cmp(common.Big1) < 0 {
		return false
	}
	// reject upper range of s values (ECDSA malleability)
	// see discussion in secp256k1/libsecp256k1/include/secp256k1.h
	if homestead && s.Cmp(secp256k1halfN) > 0 {
		return false
	}
	// Frontier: allow s to be in full N range
	return r.Cmp(secp256k1N) < 0 && s.Cmp(secp256k1N) < 0 && (v == 0 || v == 1)
}
```
