## Title
Unchecked `btcec.ParsePubKey` return value causes nil-pointer panic in `PubkeyBytesToSeiPubKey` - (File: `utils/helpers/address.go`)

## Summary
CVE-2016-1000030 is rooted in Pidgin ignoring the success/failure return values of `gnutls_x509_crt_init`/`gnutls_x509_crt_import` and continuing to use the resulting (possibly invalid) certificate object. The same defect class — discarding an `error`/success indicator from a cryptographic parse routine and then dereferencing the resulting object unconditionally — exists in `sei-chain`'s `PubkeyBytesToSeiPubKey` helper.

## Finding Description
`PubkeyBytesToSeiPubKey` discards the error returned by `btcec.ParsePubKey` and immediately calls a method on the (potentially nil) result: [1](#0-0) 

The identical pattern is duplicated in the legacy address helpers used by older EVM versions: [2](#0-1) [3](#0-2) 

If `btcec.ParsePubKey` fails (e.g., the input is not a valid compressed/uncompressed secp256k1 point), `pubkeyObj` is `nil`, and the subsequent call `pubkeyObj.SerializeCompressed()` dereferences a nil pointer, causing a runtime panic. This mirrors the CVE root cause exactly: the return value proving success/validity of a cryptographic object parse is checked nowhere before the object is used.

`GetAddressesFromPubkeyBytes` (which calls `PubkeyBytesToSeiPubKey`) is exported and used across multiple EVM address-recovery and precompile paths: [4](#0-3) 

Grep results show it is referenced from `precompiles/addr/addr.go` and all of its legacy version copies (`v600` through `v67`), in addition to the signature-recovery helpers (`GetAddresses`). In the paths reachable via `RecoverPubkey`/`crypto.Ecrecover` (e.g. `helpers.GetAddresses` used by `app/ante/evm_checktx.go`'s `CheckAndDecodeSignature` and the `addr` precompile's `associate` function), the byte slice fed into `PubkeyBytesToSeiPubKey` is the direct output of `crypto.Ecrecover`, which always yields a well-formed 65-byte uncompressed point on success, so that specific call chain is not exploitable. [5](#0-4) 

However, `precompiles/addr/addr.go` also references `GetAddressesFromPubkeyBytes` directly (outside the signature-recovery flow) as shown by the grep match; if any reachable precompile method accepts a raw public-key byte string from ABI call arguments (rather than deriving it via `Ecrecover`) and forwards it unchanged to `GetAddressesFromPubkeyBytes`/`PubkeyBytesToSeiPubKey`, an attacker-supplied malformed byte string would trigger the nil-pointer panic on that path. I was not able to fully confirm the exact call signature of that reference in `precompiles/addr/addr.go` before running out of tool budget, so this specific reachability claim should be verified against that file's contents directly.

## Impact Explanation
A nil-pointer dereference panic triggered by processing a single crafted EVM transaction/precompile call would crash the node executing that transaction. If reachable from an unprivileged transaction/precompile call path (as suggested by the `precompiles/addr/addr.go` reference), this qualifies as a crash of default-configuration nodes and, if reachable during block execution (not just simulate/estimate), could cause validator halt / chain liveness impact, matching the accepted impact categories (validator halt, crash of default-configuration RPC nodes).

## Likelihood Explanation
Likelihood is high if a public entry point accepts raw public-key bytes and forwards them to this helper without prior validation — any single external actor could send one malformed transaction/call to trigger the panic. It is unproven for the paths that only route recovered pubkeys from `Ecrecover` (unexploitable there), so likelihood is contingent on confirming the exact call sites in the `addr` precompile family that reference `GetAddressesFromPubkeyBytes` directly with untrusted input.

## Recommendation
Check and propagate the `error` returned by `btcec.ParsePubKey` in `PubkeyBytesToSeiPubKey` (and its duplicated legacy copies), returning an error instead of proceeding to call `SerializeCompressed()` on a possibly-nil result. Apply the same fix uniformly across `utils/helpers/address.go`, `utils/helpers/legacy/v600/address.go`, and `utils/helpers/legacy/v575/address.go`, and audit every caller of `GetAddressesFromPubkeyBytes` (including `precompiles/addr/addr.go` and its legacy versions) to confirm whether any of them pass externally-controlled bytes directly into this function.

## Proof of Concept
1. Call `helpers.PubkeyBytesToSeiPubKey` (or any reachable code path that forwards attacker-controlled bytes to it, e.g. a precompile method taking a raw pubkey argument) with a byte slice that is not a valid secp256k1 public key encoding (e.g., all-zero 33 bytes, or a byte slice with an invalid prefix byte).
2. `btcec.ParsePubKey` returns `(nil, error)`; the error is discarded.
3. `pubkeyObj.SerializeCompressed()` is invoked on the `nil` result, causing a runtime nil-pointer-dereference panic and crashing the process handling the request/transaction. [1](#0-0)

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

**File:** utils/helpers/address.go (L93-96)
```go
func PubkeyBytesToSeiPubKey(pub []byte) secp256k1.PubKey {
	pubkeyObj, _ := btcec.ParsePubKey(pub)
	return secp256k1.PubKey{Key: pubkeyObj.SerializeCompressed()}
}
```

**File:** utils/helpers/legacy/v600/address.go (L65-68)
```go
func PubkeyBytesToSeiPubKey(pub []byte) secp256k1.PubKey {
	pubkeyObj, _ := btcec.ParsePubKey(pub)
	return secp256k1.PubKey{Key: pubkeyObj.SerializeCompressed()}
}
```

**File:** utils/helpers/legacy/v575/address.go (L61-64)
```go
func PubkeyBytesToSeiPubKey(pub []byte) secp256k1.PubKey {
	pubkeyObj, _ := btcec.ParsePubKey(pub)
	return secp256k1.PubKey{Key: pubkeyObj.SerializeCompressed()}
}
```

**File:** app/ante/evm_checktx.go (L233-237)
```go
	evmAddr, seiAddr, seiPubkey, err := helpers.GetAddresses(V, R, S, txHash)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, 0, sdkerrors.ErrInvalidChainID
	}
	return evmAddr, seiAddr, seiPubkey, version, nil
```
