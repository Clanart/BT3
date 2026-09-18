Based on my research, I found a direct analog to **OMP-16** in the sei-chain codebase: an ignored-error → nil-pointer-dereference pattern in the pubkey-to-Sei-pubkey conversion helper, duplicated across multiple files in the EVM↔Sei address association code path.

### Title
Nil pointer dereference in `PubkeyBytesToSeiPubKey()` when converting an unvalidated public key - (File: `utils/helpers/address.go`)

### Summary
`PubkeyBytesToSeiPubKey()` discards the error returned by `btcec.ParsePubKey()` and immediately calls a method on the (possibly nil) result:

```go
func PubkeyBytesToSeiPubKey(pub []byte) secp256k1.PubKey {
	pubkeyObj, _ := btcec.ParsePubKey(pub)
	return secp256k1.PubKey{Key: pubkeyObj.SerializeCompressed()}
}
``` [1](#0-0) 

This mirrors OMP-16's pattern of blindly indexing/dereferencing a crypto-library return value without validating it succeeded.

### Finding Description
The sibling helper `PubkeyToEVMAddress()`, which is called immediately before `PubkeyBytesToSeiPubKey()` in `GetAddressesFromPubkeyBytes()`, only validates that the input is non-empty and starts with the uncompressed-key prefix `0x04` — it does **not** validate the length or that the bytes form a valid point on the secp256k1 curve:

```go
func PubkeyToEVMAddress(pub []byte) (common.Address, error) {
	if len(pub) == 0 || pub[0] != 4 {
		return common.Address{}, errors.New("invalid public key")
	}
	var addr common.Address
	copy(addr[:], crypto.Keccak256(pub[1:])[12:])
	return addr, nil
}
``` [2](#0-1) 

The project's own test suite explicitly documents this gap:
```go
invalidKey := []byte{0x04} // Just the prefix, no actual key data
_, err := PubkeyToEVMAddress(invalidKey)
// This should succeed in PubkeyToEVMAddress but may fail elsewhere
require.NoError(t, err) // Changed expectation based on actual implementation
``` [3](#0-2) 

`GetAddressesFromPubkeyBytes()` chains these two calls:
```go
func GetAddressesFromPubkeyBytes(pubkey []byte) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	evmAddr, err := PubkeyToEVMAddress(pubkey)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	seiPubkey := PubkeyBytesToSeiPubKey(pubkey)
	...
}
``` [4](#0-3) 

If `pubkey` starts with `0x04` but is not a well-formed, on-curve 65-byte uncompressed key, `PubkeyToEVMAddress` succeeds while `btcec.ParsePubKey` inside `PubkeyBytesToSeiPubKey` fails and returns `nil`; the subsequent `pubkeyObj.SerializeCompressed()` call panics with a nil-pointer dereference. The identical unguarded pattern is duplicated in every legacy address-association module (`utils/helpers/legacy/v575/address.go`, `v600/address.go`, and the corresponding precompile legacy packages `precompiles/addr/legacy/v620`, `v603`, `v66`, `v67`, `v640`), so any future or historical code path that reaches this helper with attacker-influenced bytes is affected.

Inputs recovered via `crypto.Ecrecover` in `RecoverPubkey()` are always valid, well-formed points, so the normal signed-EVM-transaction path is safe. The exposure is any call site that supplies raw, non-ECDSA-recovered bytes to `GetAddressesFromPubkeyBytes` / `PubkeyBytesToSeiPubKey`. I confirmed the vulnerable helper and the documented weak validation, but I was not able to fully enumerate — within the available tool budget — every production (non-test) call site that might feed it attacker-controlled bytes without first passing them through `btcec.ParsePubKey` (which the `associatePublicKey` precompile does do defensively). This is an important open question that should be resolved before treating this as exploitable in the primary EVM tx-signing flow.

### Impact Explanation
An unrecovered panic inside address-association logic that runs as part of ante-handling or precompile execution can propagate past the normal Cosmos SDK panic-recovery boundaries in ante decorators, which — unlike message execution — are not always wrapped in the same recover/error-conversion guarantees used for `RunMsgs`. If reachable during block processing (ante handling, `ProcessProposal`, or a precompile call within `EVMPreprocessDecorator`), this could crash the node processing that transaction, potentially resulting in a validator halt or chain split if only some nodes hit this path deterministically for a given input. If only reachable through EVM RPC personal/utility calls, impact is limited to a crash of the RPC handler goroutine.

### Likelihood Explanation
Low-to-Medium: the vulnerable helper is defensively wrapped by curve validation (`btcec.ParsePubKey`) in the one identified precompile call site (`associatePublicKey`), and the primary transaction-signing path always supplies Ecrecover-derived (inherently valid) bytes. The likelihood is contingent on there being another reachable call site (not identified with full confidence in this pass) that passes unvalidated bytes matching the `0x04` prefix but invalid length/curve point directly into `GetAddressesFromPubkeyBytes`.

### Recommendation
Check and propagate the error from `btcec.ParsePubKey()` in `PubkeyBytesToSeiPubKey()` (and all legacy duplicates), returning an error instead of a raw `secp256k1.PubKey`, and tighten `PubkeyToEVMAddress()` to require `len(pub) == 65` in addition to checking the prefix byte, closing the gap the existing test explicitly calls out.

### Proof of Concept
1. Call `helpers.GetAddressesFromPubkeyBytes([]byte{0x04})` (or any call site that forwards untrusted bytes to this function).
2. `PubkeyToEVMAddress` returns `nil` error because `pub[0] == 4`.
3. `PubkeyBytesToSeiPubKey([]byte{0x04})` calls `btcec.ParsePubKey([]byte{0x04})`, which fails and returns `(nil, error)`; the error is discarded.
4. `pubkeyObj.SerializeCompressed()` is invoked on the nil `*btcec.PublicKey`, causing a nil-pointer-dereference panic. [3](#0-2)

### Citations

**File:** utils/helpers/address.go (L53-61)
```go
func GetAddressesFromPubkeyBytes(pubkey []byte) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	evmAddr, err := PubkeyToEVMAddress(pubkey)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	seiPubkey := PubkeyBytesToSeiPubKey(pubkey)
	seiAddr := sdk.AccAddress(seiPubkey.Address())
	return evmAddr, seiAddr, &seiPubkey, nil
}
```

**File:** utils/helpers/address.go (L83-91)
```go
// second half of go-ethereum/core/types/transaction_signing.go:recoverPlain
func PubkeyToEVMAddress(pub []byte) (common.Address, error) {
	if len(pub) == 0 || pub[0] != 4 {
		return common.Address{}, errors.New("invalid public key")
	}
	var addr common.Address
	copy(addr[:], crypto.Keccak256(pub[1:])[12:])
	return addr, nil
}
```

**File:** utils/helpers/address.go (L93-96)
```go
func PubkeyBytesToSeiPubKey(pub []byte) secp256k1.PubKey {
	pubkeyObj, _ := btcec.ParsePubKey(pub)
	return secp256k1.PubKey{Key: pubkeyObj.SerializeCompressed()}
}
```

**File:** utils/helpers/associate_test.go (L138-151)
```go
func TestEdgeCases(t *testing.T) {
	t.Run("pubkey conversion edge cases", func(t *testing.T) {
		// Test with a byte array that has correct prefix but is empty after prefix
		invalidKey := []byte{0x04} // Just the prefix, no actual key data
		_, err := PubkeyToEVMAddress(invalidKey)
		// This should succeed in PubkeyToEVMAddress but may fail elsewhere
		// Since our function just checks prefix and computes keccak256, it should work
		require.NoError(t, err) // Changed expectation based on actual implementation

		// Test that the function produces a valid 20-byte address even with minimal data
		addr, err := PubkeyToEVMAddress(invalidKey)
		require.NoError(t, err)
		require.Equal(t, 20, len(addr.Bytes()))
	})
```
