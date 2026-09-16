### Title
Incorrect Authorization in `RecoverFromMessage`: single multisig key key treated as authorized signer of the whole account — ([File: api/api_kaia_transaction.go])

### Summary
The public `kaia_recoverFromMessage` / `klay_recoverFromMessage` RPC method is documented and implemented to "validate that the message is signed by ... the given account," but its underlying check (`AccountKey.ValidateMember`) for `AccountKeyWeightedMultiSig` and `AccountKeyRoleBased` accounts only verifies that the recovered public key is *a member* of the key set — it never enforces the account's configured weight threshold. This mirrors the GitLab CVE-2023-3444 bug class ("Incorrect Authorization"): a partial/insufficient set of approvers is accepted as if it constituted full authorization.

### Finding Description
`RecoverFromMessage` recovers a public key from an arbitrary signed message and calls `key.ValidateMember(pubkey, address)` to decide whether the signature legitimately represents the account: [1](#0-0) 

For a `AccountKeyWeightedMultiSig` account, `ValidateMember` only checks pubkey membership in the key list, ignoring the weight/threshold requirement that governs actual transaction authorization: [2](#0-1) 

Compare this with the actual consensus-level authorization check `Validate`, which enforces `weightedSum >= a.Threshold` before treating a set of signatures as sufficient: [3](#0-2) 

The same weaker semantics propagate through `AccountKeyRoleBased.ValidateMember`, which just OR's the per-role `ValidateMember` results without any threshold enforcement: [4](#0-3) 

So while transaction execution correctly requires the full weighted threshold via `ValidateAccountKey`/`Validate` (see `blockchain/types/transaction.go` `ValidateSender`/`ValidateFeePayer`, which call `accountkey.ValidateAccountKey` → `accKey.Validate`), the RPC-exposed `RecoverFromMessage` silently downgrades this guarantee to "signed by any one component key," while its doc comment and intended use case ("validates that the message is signed by ... the given account") implies full account-level authorization equivalent to what a threshold multisig means. [5](#0-4) [6](#0-5) 

### Impact Explanation
Any unprivileged public-RPC caller can invoke `kaia_recoverFromMessage` with a message signed by only one key of a weighted-multisig (or role-based) account and get back a "successful" recovery equal to the account address, even though that single signer's weight is below the account's configured threshold. Off-chain systems (custodians, exchanges, dApp backends, gasless/relayer authorization flows) that rely on this node RPC as an authoritative "is this message authorized by account X" check can thus be tricked into treating a minority co-signer as if it possessed full account authorization — enabling unauthorized approval of withdrawals, meta-transactions, or other value-affecting operations gated by such off-chain verification, analogous to the GitLab flaw allowing insufficient approvals to be accepted as sufficient for merging into a protected branch.

### Likelihood Explanation
Trivial to trigger: any address holding just one private key belonging to a multisig/role-based account can call the public RPC with a signed message and obtain a positive authorization result. No special privileges, staking, or on-chain transaction is required — a single JSON-RPC call is sufficient.

### Recommendation
Change `RecoverFromMessage`'s authorization semantics so it does not rely on `ValidateMember` alone for accounts with composite/weighted key types. Either: (1) restrict `RecoverFromMessage`'s success semantics to legacy/public single-key accounts and explicitly document/return an error (or a "member-only, not-threshold-satisfying" result) for `AccountKeyWeightedMultiSig`/`AccountKeyRoleBased` accounts, or (2) require callers to submit enough signatures to satisfy `accKey.Validate(...)` (the same weighted-threshold check used in consensus) before reporting a positive authorization result.

### Proof of Concept
1. Create an account with `AccountKeyWeightedMultiSig` (e.g., threshold 3, keys A, B, C each weight 1).
2. Sign an arbitrary message with only key A (weight 1, below threshold 3).
3. Call `kaia_recoverFromMessage(address, data, sigA, blockNumber)`.
4. Observe the RPC returns the account address as the "recovered signer," implying the message is authorized by the full multisig account, even though on-chain transaction validation (`ValidateAccountKey`/`Validate`) would reject the same single signature as insufficient (`weightedSum(1) < threshold(3)`).

### Citations

**File:** api/api_kaia_transaction.go (L655-676)
```go
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

**File:** blockchain/types/accountkey/account_key_weighted_multi_sig.go (L66-68)
```go
func (a *AccountKeyWeightedMultiSig) ValidateMember(recoveredKey *ecdsa.PublicKey, from common.Address) bool {
	return a.Keys.IsContainedPubkey((*PublicKeySerializable)(recoveredKey)) // temporary WeightedPublicKey
}
```

**File:** blockchain/types/accountkey/account_key_weighted_multi_sig.go (L86-141)
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

**File:** blockchain/types/accountkey/account_key.go (L116-121)
```go
func ValidateAccountKey(currentBlockNumber uint64, from common.Address, accKey AccountKey, recoveredKeys []*ecdsa.PublicKey, roleType RoleType) error {
	if !accKey.Validate(currentBlockNumber, roleType, recoveredKeys, from) {
		return errInvalidSignature
	}
	return nil
}
```

**File:** blockchain/types/transaction.go (L923-932)
```go
	accKey := p.GetKey(from)

	gasKey, err := accKey.SigValidationGas(currentBlockNumber, GetRoleTypeForValidation(tx.Type()), len(pubkey))
	if err != nil {
		return 0, err
	}

	if err := accountkey.ValidateAccountKey(currentBlockNumber, from, accKey, pubkey, GetRoleTypeForValidation(tx.Type())); err != nil {
		return 0, ErrInvalidAccountKey
	}
```
