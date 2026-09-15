### Title
Role confusion in `RecoverFromMessage` allows any sub-key of a role-based/multisig account to spoof the identity of a different, higher-privilege role - ([File: api/api_kaia_transaction.go])

### Summary
Kaia's public RPC method `RecoverFromMessage` is meant to prove that a given off-chain message/signature was produced by "one of the keys" belonging to an account, and it returns the recovered signer address as proof of ownership. For composite key types (`AccountKeyRoleBased`, `AccountKeyWeightedMultiSig`) the underlying `ValidateMember` check does not take the `RoleType` (`RoleTransaction`, `RoleAccountUpdate`, `RoleFeePayer`) into account at all — it accepts a signature from *any* sub-key registered under *any* role. This is the same class of bug as CVE-2022-22475 (identity spoofing by an authenticated-but-lower-privileged principal): a key holder who is only authorized for one narrow role (e.g., the fee-payer sub-key used by a fee-delegation counterparty) can successfully authenticate as "the account" in any off-chain or on-chain context that relies on `RecoverFromMessage`/`ValidateMember` to prove account identity/ownership, even though that key was never meant to represent the account's primary identity.

### Finding Description
`RecoverFromMessage` fetches the account's `AccountKey` via `state.GetKey(address)` and checks the signature against it using `key.ValidateMember(pubkey, address)`: [1](#0-0) 

For `AccountKeyRoleBased`, `ValidateMember` iterates over *all* sub-keys (`RoleTransaction`, `RoleAccountUpdate`, `RoleFeePayer`) regardless of role and returns `true` if any one of them matches: [2](#0-1) 

This is fundamentally different from the actual transaction-signing validation path, `AccountKeyRoleBased.Validate`, which strictly requires the signature to match the sub-key assigned to the specific `RoleType` being validated: [3](#0-2) 

So while on-chain tx validation (`ValidateSender`/`ValidateFeePayer`) correctly separates roles — a `RoleFeePayer` key can only pay fees, a `RoleAccountUpdate` key can only update keys, a `RoleTransaction` key can only send value transactions — [4](#0-3) [5](#0-4)  the `RecoverFromMessage` RPC, which is the sole way to prove "off-chain" ownership of an account without submitting a transaction, collapses all three roles into one undifferentiated identity check.

`RoleFeePayer` keys are explicitly designed to be handed out to third-party fee-delegation counterparties/services and are documented as lower-trust, single-purpose keys: "this key is used to pay tx fee when using fee-delegated transactions" [6](#0-5) . Any dApp/backend that uses `RecoverFromMessage` to authenticate a user (e.g., "prove you own address X" login flows, gasless-relay authorization, off-chain approvals) cannot distinguish whether the returned address was proven by the strong `RoleTransaction` key or by the weak, externally-shared `RoleFeePayer` key. Consequently, a party that only legitimately possesses the fee-payer sub-key (an unprivileged fee-delegation counterparty) can spoof full-account identity in any off-chain authentication/authorization workflow built on top of this RPC.

The same role-flattening also affects `AccountKeyWeightedMultiSig.ValidateMember`, which accepts any single key contained in the weighted key set as "membership" proof without applying the multisig threshold that `Validate` enforces: [7](#0-6)  versus the threshold-checked `Validate`: [8](#0-7) .

### Impact Explanation
Any single sub-key holder of a role-based key or any single participant in a weighted-multisig key can use the public `RecoverFromMessage` RPC to prove "ownership" of the entire account address, without the cooperation of other role holders or reaching the multisig threshold. In practice, this means a fee-delegation counterparty entrusted only with paying gas (a low-privilege, often externally-operated role) can impersonate the account's primary identity for any off-chain system (bridges, exchanges, dApp backends, KYC/authorization flows) that treats a successful `RecoverFromMessage` result as proof of account control. This is an authentication-bypass/identity-spoofing vector reachable by any RPC caller who already holds a narrowly-scoped, legitimately-issued key for the target account.

### Likelihood Explanation
The bug is directly reachable by any public RPC caller with knowledge of one sub-key of a role-based or multisig account — a realistic scenario since `RoleFeePayer` keys are explicitly meant to be shared with third-party fee-delegation services, and multisig participant keys are distributed among multiple parties. No special privileges, node access, or consensus-level control are required — only calling `kaia_recoverFromMessage` (or its equivalent) with a signature made by the lesser-privileged key.

### Recommendation
`RecoverFromMessage` (and `ValidateMember` in general) should accept an explicit `RoleType` parameter and only accept signatures matching the sub-key(s) assigned to that role, mirroring the strict role-checking already implemented in `AccountKeyRoleBased.Validate`/`AccountKeyWeightedMultiSig.Validate`. Alternatively, the RPC response should clearly indicate which role/sub-key produced the valid signature (and, for multisig, whether the threshold was met) so that downstream consumers cannot mistake a low-privilege role's signature for full-account authorization.

### Proof of Concept
1. Update an account to `AccountKeyRoleBased` with distinct keys for `RoleTransaction`, `RoleAccountUpdate`, and `RoleFeePayer` (e.g., using `TxTypeAccountUpdate` as shown in the account-key tests at `tests/role_based_account_test.go`).
2. Give the `RoleFeePayer` private key to an external fee-delegation counterparty (this is the documented/expected usage).
3. Have that counterparty sign an arbitrary off-chain message (EIP-191/KIP-97 prefixed) with the `RoleFeePayer` key.
4. Call `kaia_recoverFromMessage(accountAddress, message, signature, blockNumber)`.
5. Observe that the RPC returns the account's address as verified, indistinguishable from a signature made by the `RoleTransaction` key — i.e., the fee-payer-only party has successfully proven "ownership" of the account to any off-chain verifier relying on this RPC, despite never holding the primary transaction/identity key.

### Citations

**File:** api/api_kaia_transaction.go (L653-676)
```go
	key := state.GetKey(address)

	// We cannot identify if the signature has signed with EIP-191 or KIP-97 prefix without the signer's address.
	// Try ecrecover with both prefixes and validate the actual result in ValidateMember.
	var recoverErr error
	if pubkey, err := klayEcRecover(data, sig); err == nil {
		if key.ValidateMember(pubkey, address) {
			return crypto.PubkeyToAddress(*pubkey), nil
		}
	} else {
		recoverErr = err
	}
	if pubkey, err := ethEcRecover(data, sig); err == nil {
		if key.ValidateMember(pubkey, address) {
			return crypto.PubkeyToAddress(*pubkey), nil
		}
	} else {
		recoverErr = err
	}
	if recoverErr != nil {
		return common.Address{}, recoverErr
	} else {
		return common.Address{}, errors.New("Invalid signature")
	}
```

**File:** blockchain/types/accountkey/account_key_role_based.go (L47-53)
```go
// AccountKeyRoleBased represents a role-based key.
// The roles are defined like below:
// RoleTransaction   - this key is used to verify transactions transferring values.
// RoleAccountUpdate - this key is used to update keys in the account when using TxTypeAccountUpdate.
// RoleFeePayer      - this key is used to pay tx fee when using fee-delegated transactions. If an account has a key of this role and wants to pay tx fee, fee-delegated transactions should be signed by this key.
//
// If RoleAccountUpdate or RoleFeePayer is not set, RoleTransaction will be used instead by default.
```

**File:** blockchain/types/accountkey/account_key_role_based.go (L72-79)
```go
func (a *AccountKeyRoleBased) ValidateMember(recoveredKey *ecdsa.PublicKey, from common.Address) bool {
	for _, rk := range *a {
		if rk.ValidateMember(recoveredKey, from) {
			return true
		}
	}
	return false
}
```

**File:** blockchain/types/accountkey/account_key_role_based.go (L164-169)
```go
func (a *AccountKeyRoleBased) Validate(currentBlockNumber uint64, r RoleType, recoveredKeys []*ecdsa.PublicKey, from common.Address) bool {
	if len(*a) > int(r) {
		return (*a)[r].Validate(currentBlockNumber, r, recoveredKeys, from)
	}
	return a.getDefaultKey().Validate(currentBlockNumber, r, recoveredKeys, from)
}
```

**File:** blockchain/types/transaction.go (L916-932)
```go
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

**File:** blockchain/types/transaction.go (L944-967)
```go
// ValidateFeePayer finds a fee payer from a transaction.
// If the transaction is not a fee-delegated transaction, it returns an error.
func (tx *Transaction) ValidateFeePayer(signer Signer, p AccountKeyPicker, currentBlockNumber uint64) (uint64, error) {
	tf, ok := tx.data.(TxInternalDataFeePayer)
	if !ok {
		return 0, errUndefinedTxType
	}

	pubkey, err := SenderFeePayerPubkey(signer, tx)
	if err != nil {
		return 0, err
	}

	feePayer := tf.GetFeePayer()
	accKey := p.GetKey(feePayer)

	gasKey, err := accKey.SigValidationGas(currentBlockNumber, accountkey.RoleFeePayer, len(pubkey))
	if err != nil {
		return 0, err
	}

	if err := accountkey.ValidateAccountKey(currentBlockNumber, feePayer, accKey, pubkey, accountkey.RoleFeePayer); err != nil {
		return 0, ErrInvalidAccountKey
	}
```

**File:** blockchain/types/accountkey/account_key_weighted_multi_sig.go (L66-68)
```go
func (a *AccountKeyWeightedMultiSig) ValidateMember(recoveredKey *ecdsa.PublicKey, from common.Address) bool {
	return a.Keys.IsContainedPubkey((*PublicKeySerializable)(recoveredKey)) // temporary WeightedPublicKey
}
```

**File:** blockchain/types/accountkey/account_key_weighted_multi_sig.go (L86-144)
```go
func (a *AccountKeyWeightedMultiSig) Validate(currentBlockNumber uint64, r RoleType, recoveredKeys []*ecdsa.PublicKey, from common.Address) bool {
	if a.Threshold == 0 {
		return false
	}
	isIstanbul := fork.Rules(new(big.Int).SetUint64(currentBlockNumber)).IsIstanbul

	// Validation 1. if isIstanbul is true, check whether the signature number exceeds key number
	if isIstanbul && len(recoveredKeys) > len(a.Keys) {
		logger.Debug("AccountKeyWeightedMultiSig validation failed and number of signatures exceeds key number",
			"numSigs", len(recoveredKeys), "numKeys", len(a.Keys))
		return false
	}

	numberOfValidAndUniqueSigs := 0
	weightedSum := uint(0)

	// To prohibit making a signature with the same key, make a map.
	// TODO-Kaia: find another way for better performance
	pMap := make(map[string]*ecdsa.PublicKey)
	for _, bk := range recoveredKeys {
		b, err := rlp.EncodeToBytes((*PublicKeySerializable)(bk))
		if err != nil {
			logger.Warn("Failed to encode recovered public keys of the tx", "recoveredKeys", recoveredKeys)
			continue
		}
		pMap[string(b)] = bk
	}

	for _, k := range a.Keys {
		b, err := rlp.EncodeToBytes(k.Key)
		if err != nil {
			logger.Warn("Failed to encode public keys in the account", "AccountKey", a.String())
			continue
		}

		// if the registered key is included in one of the transaction signatures,
		// update weightedSum and validSigNum
		if _, ok := pMap[string(b)]; ok {
			weightedSum += k.Weight
			numberOfValidAndUniqueSigs++
		}
	}

	// Validation 2. if isIstanbul is true, check whether invalid signature exists
	if isIstanbul && numberOfValidAndUniqueSigs < len(pMap) {
		logger.Debug("AccountKeyWeightedMultiSig validation failed and invalid signature exists",
			"numberOfValidSigs", numberOfValidAndUniqueSigs, "numberOfUniqueSigs", len(pMap))
		return false
	}

	// Validation 3. check whether enough signatures are gathered
	if weightedSum < a.Threshold {
		logger.Debug("AccountKeyWeightedMultiSig validation failed and weightedSum is smaller than threshold",
			"recoveredKeys", recoveredKeys, "accountKeys", a.String(), "threshold", a.Threshold, "weighted sum", weightedSum)
		return false
	}

	return true
}
```
