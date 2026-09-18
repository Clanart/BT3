Based on my investigation, I found a concrete analog: the `PubkeyBytesToSeiPubKey` function discards the error from `btcec.ParsePubKey`, causing a nil-pointer dereference panic when called with attacker-controlled bytes that pass `PubkeyToEVMAddress`'s minimal `pub[0] != 4` check but fail full elliptic-curve point validation.

### Title
Nil-pointer dereference panic in `PubkeyBytesToSeiPubKey` via `AssociatePubKey` precompile with a malformed "uncompressed" public key - (File: `utils/helpers/address.go`)

### Summary
`PubkeyBytesToSeiPubKey` ignores the error returned by `btcec.ParsePubKey` and unconditionally calls `.SerializeCompressed()` on the result, which panics with a nil-pointer dereference if parsing fails.

### Finding Description
`PubkeyBytesToSeiPubKey` discards the parse error and dereferences the (possibly nil) result: [1](#0-0) 

`GetAddressesFromPubkeyBytes` calls this function right after only a superficial validity check performed by `PubkeyToEVMAddress`, which merely verifies the byte length is non-zero and the first byte equals `0x04` — it does **not** validate that the remaining 64 bytes represent a valid point on the secp256k1 curve: [2](#0-1) [3](#0-2) 

The reachable entry point is the `addr` precompile's `associatePublicKey` method, which decodes a hex-encoded, caller-controlled "compressed pubkey," calls `btcec.ParsePubKey` (correctly checking the error there), but then re-serializes it as *uncompressed* and passes it into `GetAddressesFromPubkeyBytes` → `PubkeyBytesToSeiPubKey`: [4](#0-3) 

While the `associatePublicKey` path itself only reaches `PubkeyBytesToSeiPubKey` with an already-validated point (since `pubKey.SerializeUncompressed()` only runs after a successful `btcec.ParsePubKey`), the underlying primitive `GetAddressesFromPubkeyBytes` is also invoked from the EIP-7702 authorization-recovery path (`RecoverAddressesFromAuthorization` → `GetAddresses` → `GetAddressesFromPubkeyBytes`), which is itself reachable from `AuthorityToPreAssociate` during normal EVM tx execution: [5](#0-4) [6](#0-5) 

In that path, `pubkey` originates from `crypto.Ecrecover` (via `RecoverPubkey`), which is expected to return a well-formed curve point whenever it succeeds; under normal operation `btcec.ParsePubKey` should therefore also succeed. I was **not able to construct or confirm** a concrete case where `crypto.Ecrecover` returns a 65-byte, `0x04`-prefixed buffer that nonetheless fails `btcec.ParsePubKey` (e.g., an off-curve point) using the tools available — this would require either a deeper audit of go-ethereum's `secp256k1` C bindings/libsecp256k1 recovery guarantees, or fuzzing, neither of which I could perform here.

### Impact Explanation
If such an input path exists (unvalidated `0x04`-prefixed bytes reaching `PubkeyBytesToSeiPubKey` without a prior successful `btcec.ParsePubKey` call), it would panic with a nil dereference. Depending on which code path is affected — the `associatePublicKey`/`associate` precompile call (guarded by `defer recover()` in `Execute`) versus the EIP-7702 authorization pre-association path (`AuthorityToPreAssociate`, part of ante-handling/EndBlock deferred work) — the panic could either be caught safely (in the precompile) or crash block processing/validator nodes if it occurs outside a recover-guarded call site, which would qualify as a validator halt.

### Likelihood Explanation
Low-to-moderate confidence. The only concretely reachable call to `PubkeyBytesToSeiPubKey` with attacker input (`associatePublicKey`) is preceded by a successful `btcec.ParsePubKey` call, so that specific path appears safe today. The EIP-7702 path relies on `crypto.Ecrecover`'s guarantee of producing curve-valid points, which I could not fully verify from the code alone.

### Recommendation
Regardless of current reachability, `PubkeyBytesToSeiPubKey` should not silently discard the `btcec.ParsePubKey` error and dereference a potentially-nil pointer. It should return an `(secp256k1.PubKey, error)` (or the caller should validate) so that any future caller — including code changes to `GetAddressesFromPubkeyBytes` or new precompile/EVM entry points — cannot trigger a panic on malformed input. This is a defense-in-depth fix analogous to CVE-2016-2524's root cause of trusting unchecked parser output. A background engineer should audit all callers of `PubkeyBytesToSeiPubKey` (`utils/helpers/address.go:93`, `utils/helpers/legacy/v575/address.go:61`, `utils/helpers/legacy/v600/address.go`) and propagate the parse error properly instead of ignoring it with `_`.

### Proof of Concept
Not confirmed as directly triggerable through the currently traced entry points; a proof of concept would require identifying (or fuzzing for) a byte sequence that `crypto.Ecrecover`/go-ethereum's signature recovery can produce which `btcec.ParsePubKey` rejects, which was not established during this review.

### Citations

**File:** utils/helpers/address.go (L44-61)
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

**File:** precompiles/addr/addr.go (L205-236)
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
```
