### Title
Integer-overflow/underflow in `AccountKeyWeightedMultiSig.SigValidationGas` allows fee/gas-accounting bypass for signature-validation work - ([File: blockchain/types/accountkey/account_key_weighted_multi_sig.go])

### Summary
The Xen advisory describes a bounds check that only constrains an attacker-controlled count to a multiple of the correct limit instead of the limit itself, letting arithmetic on that count run past the space that was actually reserved. Kaia's `AccountKeyWeightedMultiSig.SigValidationGas` has the analogous defect: after the Istanbul hard fork it computes the signature-validation gas directly from the caller-supplied `numSigs` (the number of ECDSA signatures attached to the transaction) without ever verifying that `numSigs` is bounded by `MaxNumKeysForMultiSig` (or any sane limit), even though the function does perform that exact bound check for `numKeys` a few lines above.

### Finding Description
`SigValidationGas` bounds `numKeys` (the number of keys registered on the account) against `MaxNumKeysForMultiSig` but does not apply any analogous bound to `numSigs` in the Istanbul branch: [1](#0-0) 

`numSigs` is not derived from the account's on-chain state — it is `len(pubkey)`, the number of public keys recovered from the signatures attached to the submitted transaction, which is fully attacker-controlled: [2](#0-1) 

Because `SigValidationGas` is called *before* `ValidateAccountKey`/`Validate()` (which is the only place that checks `len(recoveredKeys) > len(a.Keys)`), an attacker can submit a transaction signed with an arbitrarily large number of signatures. `numSigs-1` is computed as an `int` and then converted to `uint64` and multiplied by `params.TxValidationGasPerKey`. Go's `uint64` multiplication wraps silently on overflow, so for a sufficiently large (attacker-chosen) `numSigs`, the resulting `gasKey` can wrap around to an arbitrarily small value — effectively decoupling the gas charged for signature validation from the real computational cost of validating that many signatures (each signature still requires an actual ECDSA public-key recovery, performed unconditionally while building `pubkey` in `ValidateSender`).

This mirrors the CVE's root cause exactly: a bound that is enforced against the wrong/insufficient quantity (`numKeys` instead of `numSigs`) lets a derived value overrun the space/resource actually reserved for it (the gas budget), because the arithmetic based on the unchecked value silently overflows.

### Impact Explanation
The gas charged (`sigValGas`, part of `intrinsicGas`) no longer reflects the actual computational cost of the transaction. This is a fee-accounting bypass: `tx.Gas() < intrinsicGas+sigValGas` (the intrinsic-gas admission check in the pool/state transition) can be satisfied with a deflated `sigValGas`, letting the transaction be admitted and executed for less gas than its real signature-verification cost. This directly falls under "fee ... abuse" and can also enable "acceptance of an invalid transaction" (one that should have failed the intrinsic gas floor).

### Likelihood Explanation
Any unprivileged transaction sender whose account uses `AccountKeyWeightedMultiSig` (a normal, permissionless account-key type) can craft a transaction with an oversized signature list. No special privileges, validator collusion, or node compromise are required — this is reachable purely through `AsMessageWithAccountKeyPicker` → `ValidateSender` → `SigValidationGas`, which runs for every submitted transaction from a multisig account.

### Recommendation
In `AccountKeyWeightedMultiSig.SigValidationGas`, validate `numSigs` the same way `numKeys` is validated (e.g., reject if `numSigs <= 0` or `numSigs > MaxNumKeysForMultiSig`) before performing the multiplication, and use overflow-safe arithmetic (e.g. `math.SafeMul`/`SafeSub`, as already used elsewhere in the codebase, see `blockchain/types/transaction_test.go` `TestGasOverflow`) instead of raw `uint64` multiplication.

### Proof of Concept
1. Create an account whose key is `AccountKeyWeightedMultiSig` with a small number of registered keys (e.g. 2), threshold 1.
2. Craft a transaction (e.g. value transfer) "signed" with a very large `TxSignatures` list (attacker controls the raw V/R/S array attached to the tx; each entry recovers to *some* public key via `SenderPubkey`/`ecrecover`, valid or not — `SigValidationGas` is invoked with `numSigs = len(pubkey)` before `Validate()`/`ValidateAccountKey()` rejects the mismatched signature count).
3. Choose the signature count `N` such that `uint64(N-1) * params.TxValidationGasPerKey` overflows `uint64` and wraps to a small value.
4. Submit the transaction with a `Gas()` just above `intrinsicGas + (wrapped small sigValGas)`. The intrinsic-gas check in `blockchain/tx_pool.go` (`tx.Gas() < intrGas+sigValGas`) passes despite the real signature-recovery cost of `N` ECDSA operations, demonstrating the gas/fee-accounting bypass caused by the missing bound on `numSigs`.

Note: I could not fully trace whether any upstream layer (RLP size limits, `MaxTxDataSize`, or list-length limits on `TxSignatures`) caps the number of signatures that can be attached to a transaction before reaching `SigValidationGas`; that would determine the minimum practical `N` needed to trigger the wraparound. This should be verified in a full development environment before treating the PoC as final.

### Citations

**File:** blockchain/types/accountkey/account_key_weighted_multi_sig.go (L161-178)
```go
func (a *AccountKeyWeightedMultiSig) SigValidationGas(currentBlockNumber uint64, r RoleType, numSigs int) (uint64, error) {
	numKeys := uint64(len(a.Keys))
	if numKeys > MaxNumKeysForMultiSig {
		logger.Warn("validation failed due to the number of keys in the account is larger than the limit.",
			"account", a.String())
		return 0, kerrors.ErrMaxKeysExceedInValidation
	}
	if numKeys == 0 {
		logger.Error("should not happen! numKeys is equal to zero!")
		return 0, kerrors.ErrZeroLength
	}

	isIstanbul := fork.Rules(new(big.Int).SetUint64(currentBlockNumber)).IsIstanbul
	if isIstanbul {
		return uint64(numSigs-1) * params.TxValidationGasPerKey, nil
	}
	return (numKeys - 1) * params.TxValidationGasPerKey, nil
}
```

**File:** blockchain/types/transaction.go (L914-932)
```go
	pubkey, err := SenderPubkey(signer, tx)
	if err != nil {
		return 0, err
	}
	txfrom, ok := tx.data.(TxInternalDataFrom)
	if !ok {
		return 0, errNotTxInternalDataFrom
	}
	from := txfrom.GetFrom()
	accKey := p.GetKey(from)

	gasKey, err := accKey.SigValidationGas(currentBlockNumber, GetRoleTypeForValidation(tx.Type()), len(pubkey))
	if err != nil {
		return 0, err
	}

	if err := accountkey.ValidateAccountKey(currentBlockNumber, from, accKey, pubkey, GetRoleTypeForValidation(tx.Type())); err != nil {
		return 0, ErrInvalidAccountKey
	}
```
