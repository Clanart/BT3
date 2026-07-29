## Analysis

The Trail-of-Bits finding is about **blind signing**: a UI dialog lets a user sign arbitrary bytes (`signText`) that are indistinguishable from a real transaction hash, so an attacker can get a "harmless-looking" signature that is actually valid for authorizing a fund-moving operation elsewhere. The direct structural analog in this Cosmos EVM codebase is the **dual-mode signature verification** implemented for `eth_secp256k1` keys, combined with the JSON-RPC `eth_sign`/`personal_sign` endpoints that sign raw, attacker-supplied bytes with no domain separation.

### Title
Cross-protocol signature confusion via `eth_secp256k1.PubKey.VerifySignature`'s dual raw/EIP-712 acceptance combined with unprefixed `eth_sign`/`personal_sign` — (File: `crypto/ethsecp256k1/ethsecp256k1.go`)

### Summary
`PubKey.VerifySignature` for the chain's native `eth_secp256k1` key type accepts a signature as valid if it verifies either against the raw Keccak256 hash of the message **or** against the Keccak256 hash of the EIP-712-wrapped representation of that same message: [1](#0-0) 

`verifySignatureECDSA` is the exact same "hash-and-verify" primitive used by ordinary raw-data signing: [2](#0-1) 

Meanwhile, the JSON-RPC `eth_sign`/`personal_sign` implementations sign **arbitrary attacker/dApp-supplied bytes directly**, with no `"\x19Ethereum Signed Message:\n"` prefix and no content restriction, unlike the go-ethereum standard (which the accompanying `EcRecover` docstring/implementation actually assumes uses `accounts.TextHash`): [3](#0-2) [4](#0-3) [5](#0-4) 

### Finding Description
A user can be shown a hex/opaque payload in a wallet's "sign message" dialog (`eth_sign` / `personal_sign`) and asked to sign it "to verify ownership," exactly as in the ToB exploit scenario. Because `Backend.Sign` signs the raw bytes with no prefix, and `ethsecp256k1.PubKey.VerifySignature` treats a raw-message ECDSA signature as an acceptable signature for a Cosmos SDK transaction whenever the signed bytes equal a transaction's sign-bytes (used by the standard Cosmos SDK `SigVerificationDecorator`, which calls `pubKey.VerifySignature(signBytes, sig)` for `SIGN_MODE_DIRECT`/`SIGN_MODE_LEGACY_AMINO_JSON`), any raw-message signature the victim believes is "just a login/ownership proof" can double as a fully valid signature authorizing an arbitrary bank transfer, ERC20 conversion, staking, or precompile-invoked transaction — provided the attacker crafts the "message to sign" to be byte-identical to the SignDoc of the malicious transaction. There is no domain separation (no prefix, no purpose tag) distinguishing "arbitrary application message" from "transaction authorization" for this key type, which is the same missing invariant flagged in the source report (BLAKE-hash-as-signable-text there; raw-txSignBytes-as-signable-text here).

### Impact Explanation
If exploited, this allows unauthorized extraction/theft of user funds: a user's `eth_secp256k1` signature obtained under the pretext of an off-chain message (dApp login, ownership verification, etc.) can be repurposed by an attacker as a valid transaction authorization, moving native balances, ERC20/precompile-mediated balances, or staking/distribution value without the user's informed consent — matching the "Critical … theft, or unauthorized extraction of user funds" impact category.

### Likelihood Explanation
Exploitation requires the attacker to control (or predict) the exact bytes to be signed and get the victim to call `eth_sign`/`personal_sign` on those bytes (a phishing/social-engineering step, same difficulty rating — High — as in the original ToB report). It does not require any privileged access, malicious validator, or protocol-level bug; it only requires normal use of standard JSON-RPC signing endpoints exposed to any wallet/dApp interaction, which is an ordinary unprivileged transaction/signing flow.

### Recommendation
- Short term: Prefix all `eth_sign`/`personal_sign` payloads with the standard `"\x19Ethereum Signed Message:\n"` EIP-191 prefix (as go-ethereum does) before hashing/signing in `rpc/backend/sign_tx.go`'s `Sign`, so raw-signed bytes can never collide with a transaction's raw sign-bytes or EIP-712 hash.
- Long term: Remove or gate the "verify against raw ECDSA over unprefixed message" fallback path in `ethsecp256k1.PubKey.VerifySignature`/`verifySignatureECDSA`, or ensure it is only reachable from the dedicated EIP-712 tx-verification decorator rather than being a generic `cryptotypes.PubKey.VerifySignature` implementation invoked by the SDK's general-purpose `SigVerificationDecorator`. Add explicit domain-separation tags distinguishing "application message" signing from "transaction" signing contexts.

### Proof of Concept
1. Attacker crafts a `MsgSend`/`MsgConvertERC20`/etc. transaction from the victim's address, encodes it per `SIGN_MODE_DIRECT` (or Amino) to get `signBytes`.
2. Attacker disguises `signBytes` as a hex "verification code" and asks the victim's wallet to call `personal_sign`/`eth_sign` on it (e.g., via a malicious dApp).
3. `Backend.Sign` (`rpc/backend/sign_tx.go:127-145`) signs the raw bytes with the victim's `eth_secp256k1` key, producing a signature valid under `verifySignatureECDSA(signBytes, sig)`.
4. Attacker assembles a Cosmos SDK `Tx` using `signBytes` and the obtained signature, and broadcasts it. Standard SDK ante signature verification calls `pubKey.VerifySignature(signBytes, sig)`, which succeeds via the raw-ECDSA path in `ethsecp256k1.go:213-215`/`240-248`, authorizing the transaction and moving the victim's funds.

### Citations

**File:** crypto/ethsecp256k1/ethsecp256k1.go (L207-215)
```go
// VerifySignature verifies that the ECDSA public key created a given signature over
// the provided message. It will calculate the Keccak256 hash of the message
// prior to verification and approve verification if the signature can be verified
// from either the original message or its EIP-712 representation.
//
// CONTRACT: The signature should be in [R || S] format.
func (pubKey PubKey) VerifySignature(msg, sig []byte) bool {
	return pubKey.verifySignatureECDSA(msg, sig) || pubKey.verifySignatureAsEIP712(msg, sig)
}
```

**File:** crypto/ethsecp256k1/ethsecp256k1.go (L239-248)
```go
// Perform standard ECDSA signature verification for the given raw bytes and signature.
func (pubKey PubKey) verifySignatureECDSA(msg, sig []byte) bool {
	if len(sig) == crypto.SignatureLength {
		// remove recovery ID (V) if contained in the signature
		sig = sig[:len(sig)-1]
	}

	// the signature needs to be in [R || S] format when provided to VerifySignature
	return crypto.VerifySignature(pubKey.Key, crypto.Keccak256Hash(msg).Bytes(), sig)
}
```

**File:** rpc/backend/sign_tx.go (L126-145)
```go
// Sign signs the provided data using the private key of address via Geth's signature standard.
func (b *Backend) Sign(address common.Address, data hexutil.Bytes) (hexutil.Bytes, error) {
	from := sdk.AccAddress(address.Bytes())

	_, err := b.ClientCtx.Keyring.KeyByAddress(from)
	if err != nil {
		b.Logger.Error("failed to find key in keyring", "address", address.String())
		return nil, fmt.Errorf("%s; %s", keystore.ErrNoMatch, err.Error())
	}

	// Sign the requested hash with the wallet
	signature, _, err := b.ClientCtx.Keyring.SignByAddress(from, data, signingtypes.SignMode_SIGN_MODE_TEXTUAL)
	if err != nil {
		b.Logger.Error("keyring.SignByAddress failed", "address", address.Hex())
		return nil, err
	}

	signature[crypto.RecoveryIDOffset] += 27 // Transform V from 0/1 to 27/28 according to the yellow paper
	return signature, nil
}
```

**File:** rpc/namespaces/ethereum/personal/api.go (L118-130)
```go
// Sign calculates an Ethereum ECDSA signature for:
// keccak256("\x19Ethereum Signed Message:\n" + len(message) + message))
//
// Note, the produced signature conforms to the secp256k1 curve R, S and V values,
// where the V value will be 27 or 28 for legacy reasons.
//
// The key used to calculate the signature is decrypted with the given password.
//
// https://github.com/ethereum/go-ethereum/wiki/Management-APIs#personal_sign
func (api *PrivateAccountAPI) Sign(_ context.Context, data hexutil.Bytes, addr common.Address, _ string) (hexutil.Bytes, error) {
	api.logger.Debug("personal_sign", "data", data, "address", addr.String())
	return api.backend.Sign(addr, data)
}
```

**File:** rpc/namespaces/ethereum/eth/api.go (L399-403)
```go
// Sign signs the provided data using the private key of address via Geth's signature standard.
func (e *PublicAPI) Sign(address common.Address, data hexutil.Bytes) (hexutil.Bytes, error) {
	e.logger.Debug("eth_sign", "address", address.Hex(), "data", common.Bytes2Hex(data))
	return e.backend.Sign(address, data)
}
```
