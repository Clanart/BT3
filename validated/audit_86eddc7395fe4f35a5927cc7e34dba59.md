### Title
Integer Underflow in `AccountKeyWeightedMultiSig.SigValidationGas` via Unsigned Cast of `numSigs-1` - (File: blockchain/types/accountkey/account_key_weighted_multi_sig.go)

### Summary
`AccountKeyWeightedMultiSig.SigValidationGas` computes the post-Istanbul signature-validation gas charge as `uint64(numSigs-1) * params.TxValidationGasPerKey`, where `numSigs` is a caller-supplied `int` derived from the number of ECDSA public keys recovered from a transaction's raw signatures. If `numSigs` is `0`, the Go expression `numSigs-1` evaluates to `-1` in `int` arithmetic *before* the conversion to `uint64`, so the cast wraps to `18446744073709551615` (`2^64-1`). This is structurally the same bug class as the GoBGP `CVE-2026-7736` issue (CWE-191): an unsigned quantity is derived from a subtraction that is not checked for going negative, producing a huge wrapped value instead of an error.

### Finding Description
```go
// blockchain/types/accountkey/account_key_weighted_multi_sig.go:161-178
func (a *AccountKeyWeightedMultiSig) SigValidationGas(currentBlockNumber uint64, r RoleType, numSigs int) (uint64, error) {
	numKeys := uint64(len(a.Keys))
	if numKeys > MaxNumKeysForMultiSig {
		...
	}
	if numKeys == 0 {
		...
	}
	isIstanbul := fork.Rules(new(big.Int).SetUint64(currentBlockNumber)).IsIstanbul
	if isIstanbul {
		return uint64(numSigs-1) * params.TxValidationGasPerKey, nil
	}
	return (numKeys - 1) * params.TxValidationGasPerKey, nil
}
``` [1](#0-0) 

The function is reached from `Transaction.ValidateSender`, which is invoked while validating any submitted transaction whose sender/fee-payer account uses a `WeightedMultiSig` (or `RoleBased` wrapping one) `AccountKey`:

```go
// blockchain/types/transaction.go:914-932
pubkey, err := SenderPubkey(signer, tx)
if err != nil {
    return 0, err
}
...
gasKey, err := accKey.SigValidationGas(currentBlockNumber, GetRoleTypeForValidation(tx.Type()), len(pubkey))
if err != nil {
    return 0, err
}
if err := accountkey.ValidateAccountKey(currentBlockNumber, from, accKey, pubkey, GetRoleTypeForValidation(tx.Type())); err != nil {
    return 0, ErrInvalidAccountKey
}
``` [2](#0-1) 

`SigValidationGas` is called with `numSigs = len(pubkey)` **before** `ValidateAccountKey` performs the actual threshold/signature checks (which would normally reject an account key with zero valid signatures). If a transaction can be constructed such that `SenderPubkey` returns zero recovered public keys without an error (e.g., an RLP-decoded signature list that is empty), `numSigs` is `0` at the point `SigValidationGas` is invoked, triggering the underflow described above and producing a gas value near `uint64` max (or an unpredictable small value once multiplied by `params.TxValidationGasPerKey`, since unsigned multiplication silently wraps in Go).

The identical `RoleBased` wrapper simply forwards `numSigs` unchanged to the underlying key's `SigValidationGas`, so the same bug is reachable for role-based accounts with a `WeightedMultiSig` role key: [3](#0-2) 

### Impact Explanation
`SigValidationGas`'s return value (`gasKey`) is added to the transaction's required intrinsic gas both in the tx-pool admission path and in state-transition intrinsic-gas accounting: [4](#0-3) [5](#0-4) 

If the underflow yields a near-`math.MaxUint64` gas requirement, the affected transaction is effectively unpayable/rejected — a denial-of-validation rather than a value-theft outcome. However, because the final result is `uint64(numSigs-1) * params.TxValidationGasPerKey`, and unsigned multiplication in Go wraps silently on overflow, the actual returned gas value depends on `params.TxValidationGasPerKey` and is not simply "very large" — it could also wrap around to a small, attacker-influenced number, under-charging the required intrinsic gas for signature validation. An under-charge would let a transaction be admitted/executed with less gas reserved for signature verification than intended, which is a state-transition/gas-accounting correctness violation reachable by any ordinary transaction sender, matching the CWE-191 class of the source advisory. I could not fully verify from the available index whether `params.TxValidationGasPerKey`'s concrete value causes wraparound to a small number or keeps the result astronomically large in all cases — this depends on a compile-time constant I did not confirm.

### Likelihood Explanation
Reaching this path requires constructing a transaction whose `SenderPubkey(signer, tx)` returns an empty (zero-length) `pubkey` slice with no error, for an account whose `AccountKey` is `AccountKeyWeightedMultiSig` (directly or via `AccountKeyRoleBased`), on a block where `IsIstanbul` is active. I was not able to confirm from the indexed code whether Kaia's `TxSignatures`/`Signer.SenderPubkey` implementations for multi-signature-capable transaction types reject an empty raw-signature list before reaching `ValidateSender`, or whether such a tx would fail RLP/signature decoding earlier. This is the key open uncertainty affecting exploitability; if empty signature lists are already rejected upstream (e.g., during RLP decoding or `Sender`/`SenderPubkey`), this path is unreachable and the finding would not be practically exploitable, though the code pattern itself remains an unguarded unsigned-underflow bug.

### Recommendation
- In `AccountKeyWeightedMultiSig.SigValidationGas`, validate `numSigs >= 1` before computing `uint64(numSigs-1)`, returning an explicit error (e.g., `kerrors.ErrZeroLength`) for `numSigs <= 0`, mirroring the existing `numKeys == 0` guard.
- Apply the same guard in `AccountKeyRoleBased.SigValidationGas`/any other wrapper that forwards `numSigs`.
- Move the `ValidateAccountKey` (threshold/signature) check in `Transaction.ValidateSender` and `ValidateFeePayer` to occur before or atomically with the gas calculation, so that gas is never computed from an already-invalid signature count.
- Add unit tests covering `numSigs == 0` explicitly (the existing test in `account_key_test.go` only covers `numSigs >= 1`).

### Proof of Concept
Conceptual (not confirmed executable end-to-end due to the uncertainty noted above):
1. Register an account with `AccountKeyWeightedMultiSig` (or `AccountKeyRoleBased` wrapping one) via `TxTypeAccountUpdate`.
2. Craft/submit a Kaia-type transaction (e.g., `TxTypeValueTransfer`) for that sender with a `TxSignatures` payload that RLP-decodes to an empty signature list, such that `SenderPubkey` returns `len(pubkey) == 0` without error.
3. `Transaction.ValidateSender` calls `accKey.SigValidationGas(blockNum, role, 0)`.
4. Inside `SigValidationGas`, `uint64(0-1)` evaluates to `18446744073709551615`, and `18446744073709551615 * params.TxValidationGasPerKey` wraps around per Go's unsigned overflow semantics, producing an unpredictable `gasKey` value instead of an error — before `ValidateAccountKey` has a chance to reject the zero-signature transaction. [6](#0-5)

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

**File:** blockchain/types/accountkey/account_key_role_based.go (L194-209)
```go
func (a *AccountKeyRoleBased) SigValidationGas(currentBlockNumber uint64, r RoleType, numSigs int) (uint64, error) {
	var key AccountKey
	// Set the key used to sign for validation.
	if len(*a) > int(r) {
		key = (*a)[r]
	} else {
		key = a.getDefaultKey()
	}

	gas, err := key.SigValidationGas(currentBlockNumber, r, numSigs)
	if err != nil {
		return 0, err
	}

	return gas, nil
}
```

**File:** blockchain/tx_pool.go (L989-998)
```go
	}

	intrGas, err := tx.IntrinsicGas(pool.currentBlockNumber)
	sigValGas := gasFrom + gasFeePayer
	if err != nil {
		return err
	}
	if tx.Gas() < intrGas+sigValGas {
		return ErrIntrinsicGas
	}
```

**File:** blockchain/state_transition.go (L549-564)
```go
	// Check clauses 4-5, subtract intrinsic gas if everything is correct
	validatedGas := msg.ValidatedGas()
	if st.gas < validatedGas.IntrinsicGas {
		return nil, ErrIntrinsicGas
	}
	if rules.IsPrague {
		floorDataGas, err = FloorDataGas(msg.Type(), msg.Data(), validatedGas.SigValidateGas)
		if err != nil {
			return nil, err
		}
		if msg.Gas() < floorDataGas {
			return nil, fmt.Errorf("%w: have %d, want %d", ErrFloorDataGas, st.gas, floorDataGas)
		}
	}
	// SigValidationGas is already inclduded in IntrinsicGas
	st.gas -= validatedGas.IntrinsicGas
```
