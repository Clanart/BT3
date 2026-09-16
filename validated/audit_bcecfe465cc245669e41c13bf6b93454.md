## Analog Found: Index-out-of-range panic (DoS) in `AccountKeyRoleBased` validation when the role-key array is empty

### Title
Empty-array index-out-of-range panic in `AccountKeyRoleBased.Validate`/`getDefaultKey` causes node DoS - (File: `blockchain/types/accountkey/account_key_role_based.go`)

### Summary
`AccountKeyRoleBased` is decoded from user-submitted transactions (`AccountUpdate`, `FeeDelegatedAccountUpdate`, `FeeDelegatedAccountUpdateWithRatio`) without ever rejecting a zero-length role-key list, and later code unconditionally indexes into that (possibly empty) slice, causing a runtime panic. This is the Go analog of the reported Cesanta MJS SEGV: malformed/edge-case input reaching an unchecked memory/array access and crashing the process.

### Finding Description
`AccountKeyRoleBased` is a slice type `[]AccountKey` representing per-role keys (`RoleTransaction`, `RoleAccountUpdate`, `RoleFeePayer`). Its RLP decoder does not enforce a minimum length: [1](#0-0) 

The struct also exposes an `errKeyLengthZero` error variable, implying an intended zero-length check exists somewhere, but the actual `DecodeRLP` implementation shown above performs no such check — an RLP-encoded empty list `[]` decodes successfully into `*a = AccountKeyRoleBased{}` (length 0).

The RPC-side sanity check `checkAccountKeyZeroValues` also does not reject an empty `AccountKeyRoleBased`; it only iterates over existing entries and validates weights/thresholds of *inner* keys — an empty slice simply produces zero loop iterations and returns `nil`: [2](#0-1) 

Once an account is (or would be) assigned an empty `AccountKeyRoleBased`, any subsequent signature validation for that account unconditionally indexes into the slice: [3](#0-2) 

`Validate` checks `len(*a) > int(r)`; for an empty slice this is always false, so it falls through to `getDefaultKey()`, which performs `(*a)[RoleTransaction]` i.e. `(*a)[0]` on a zero-length slice — a Go index-out-of-range panic (the Go runtime equivalent of the SEGV described in the CVE). The same unchecked-index pattern also exists in `SigValidationGas`: [4](#0-3) 

This `Validate`/`SigValidationGas` path is exercised whenever a transaction from (or fee-delegated to) an account holding this key type is verified — i.e., on ordinary transaction admission/execution, not just account-update processing.

### Impact Explanation
A panic triggered while processing a transaction in the pool-admission or block-execution path can crash the goroutine handling it; if it is not wrapped by a `recover()` at that specific call site, it can crash the node process, producing a Denial-of-Service against any node that processes such a transaction (or a block containing it), consistent with the reported CVSS profile (`AV:L/AC:L/... /A:H`, Denial of Service via SEGV analog). Because the trigger is a normal `AccountUpdate`-family transaction that any unprivileged sender can construct and submit, and because subsequent transactions from/to that account would repeatedly trigger the same panic, this could be used to reliably crash or destabilize nodes that accept and process the account.

### Likelihood Explanation
Medium-High reachability: constructing an `AccountUpdate` transaction with an RLP-encoded empty list for the `AccountKeyRoleBased` field requires no special privileges — only a valid signature over the (malformed) key payload, which an attacker fully controls. Whether this is exploitable end-to-end depends on two points I could not fully verify with the available tools/time: (1) whether some other layer (e.g., `NewAccountKey`/`NewTxInternalDataWithMap`/tx-pool `Validate()` checks) rejects a role-based key of length 0 before it is persisted to state, and (2) whether the ultimate call site of `Validate`/`SigValidationGas` (state transition / tx pool validation) wraps execution in a `recover()` (as `NewTransactionWithMap` does for a different code path) that would downgrade the panic to an error instead of crashing the process. I found `errKeyLengthZero`/`errKeyShouldNotBeNilOrCompositeType` declared in the same file, suggesting an intended check exists but I could not locate its call site within the given search budget, so it's unclear whether it's actually invoked on this path.

### Recommendation
- Add an explicit length check in `AccountKeyRoleBased.DecodeRLP`, `NewAccountKeyRoleBasedWithValues`, and `UnmarshalJSON` to reject zero-length role-key arrays (returning `errKeyLengthZero`) before they are accepted into a transaction or stored to state.
- Make `checkAccountKeyZeroValues` (and the equivalent state-transition-side validation) explicitly reject `len(roleBasedKey) == 0`.
- Harden `getDefaultKey()` and `SigValidationGas` to bounds-check `len(*a)` before indexing, returning an error/`false` instead of panicking, as defense in depth regardless of upstream validation.

### Proof of Concept
Conceptual reproduction (exact reachability of the panic depends on the unresolved points above):
1. Craft an `AccountUpdate` transaction (or `FeeDelegatedAccountUpdate*`) whose `AccountKey` field RLP-encodes an `AccountKeyType` byte for `AccountKeyTypeRoleBased` followed by an RLP-encoded empty list `[]` for the role-key array.
2. Sign the transaction normally.
3. Submit the transaction to a node. If it is decoded via `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP`, it succeeds with `*a` set to an empty slice, per: [1](#0-0) 
4. Once this account key is used for signature validation (e.g., the account subsequently sends a transaction, or the same account update transaction's signature is validated against role `RoleAccountUpdate`), `Validate`/`getDefaultKey` executes `(*a)[0]` on the empty slice, panicking: [3](#0-2) 

Because I was unable to fully trace whether an existing upstream check (`errKeyLengthZero`) or a `recover()` wrapper intercepts this before it reaches production code paths, I recommend this be verified in a live/test node environment (e.g., via a Devin session with full repo/test access) to confirm whether the panic is reachable end-to-end and whether it terminates the process or is caught.

### Citations

**File:** blockchain/types/accountkey/account_key_role_based.go (L120-138)
```go
func (a *AccountKeyRoleBased) DecodeRLP(s *rlp.Stream) error {
	enc := [][]byte{}
	if err := s.Decode(&enc); err != nil {
		return err
	}

	keys := make([]AccountKey, len(enc))
	for i, b := range enc {
		serializer := NewAccountKeySerializer()
		if err := rlp.DecodeBytes(b, &serializer); err != nil {
			return err
		}
		keys[i] = serializer.key
	}

	*a = (AccountKeyRoleBased)(keys)

	return nil
}
```

**File:** blockchain/types/accountkey/account_key_role_based.go (L164-173)
```go
func (a *AccountKeyRoleBased) Validate(currentBlockNumber uint64, r RoleType, recoveredKeys []*ecdsa.PublicKey, from common.Address) bool {
	if len(*a) > int(r) {
		return (*a)[r].Validate(currentBlockNumber, r, recoveredKeys, from)
	}
	return a.getDefaultKey().Validate(currentBlockNumber, r, recoveredKeys, from)
}

func (a *AccountKeyRoleBased) getDefaultKey() AccountKey {
	return (*a)[RoleTransaction]
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

**File:** api/api_kaia.go (L188-199)
```go
	case accountkey.AccountKeyTypeRoleBased:
		if isNested {
			return errors.New("roleBasedKey cannot contains a roleBasedKey as a role key")
		}
		roleBasedKey, _ := key.(*accountkey.AccountKeyRoleBased)
		for _, roleKey := range *roleBasedKey {
			if err := checkAccountKeyZeroValues(roleKey, true); err != nil {
				return err
			}
		}
	}
	return nil
```
