This confirms the finding: `CheckReplacable` is called from all three account-update transaction types (`TxInternalDataAccountUpdate`, `TxInternalDataFeeDelegatedAccountUpdate`, `TxInternalDataFeeDelegatedAccountUpdateWithRatio`), and also recursively for each role slot inside `AccountKeyRoleBased.CheckUpdatable`. The tests at `tests/account_keytype_test.go:1421-1500` confirm `AccountKeyFail` sub-keys can freely be *installed* into role slots, but there is no test covering removal/replacement of an existing `Fail` sub-key with a different type — which is exactly the code path that skips `CheckUpdatable`.

### Title
Authorization bypass of the immutable `AccountKeyFail` restriction via type-mismatched account key update - ([File: blockchain/types/accountkey/account_key.go])

### Summary
`accountkey.CheckReplacable` dispatches to `oldKey.CheckUpdatable(newKey, ...)` only when `oldKey.Type() == newKey.Type()`. When the submitted new key has a *different* type than the existing key, `CheckReplacable` skips the old key's updatability check entirely and only validates that the new key is `CheckInstallable`. This means the explicit "not updatable" invariant enforced by `AccountKeyFail.CheckUpdatable` (which unconditionally returns `kerrors.ErrAccountKeyFailNotUpdatable`) is never invoked whenever the attacker simply picks a new key of a different type, defeating the restriction.

### Finding Description
`AccountKeyFail` is documented and implemented as a permanently non-updatable account key: `CheckUpdatable` always returns `ErrAccountKeyFailNotUpdatable` regardless of the proposed new key [1](#0-0) . Its primary purpose is to permanently disable signature-based control of an address/role — for smart contract accounts it is meant to guarantee "the only way to take tokens from a smart contract account is using `transfer()` in the smart contract code" [2](#0-1) , and it can also be assigned to any individual role slot of a `RoleBased` key to permanently disable that role.

However, the dispatcher that all three account-update transaction types rely on to authorize a key replacement is type-gated: [3](#0-2) 

If `oldKey.Type()` differs from `newKey.Type()`, the function never calls `oldKey.CheckUpdatable`; it only checks whether the *new* key is installable via `newKey.CheckInstallable()`. Since `AccountKeyFail.CheckUpdatable` is the sole enforcement point for its immutability guarantee, choosing any different-typed new key (e.g. `AccountKeyPublic`) bypasses that guarantee completely.

This dispatcher is invoked by:
- `TxInternalDataAccountUpdate.Validate` [4](#0-3) 
- `TxInternalDataFeeDelegatedAccountUpdate.Validate` [5](#0-4) 
- `TxInternalDataFeeDelegatedAccountUpdateWithRatio.Validate` [6](#0-5) 

and recursively, for each role slot, by `AccountKeyRoleBased.CheckUpdatable`: [7](#0-6) , where the `default` branch calls `CheckReplacable((*a)[i], (*newKey)[i], ...)` for any existing non-nil sub-key, including one previously set to `AccountKeyTypeFail`.

For smart contract accounts specifically, the intended direct-update path is separately blocked by `validate7702`, which rejects `TxTypeAccountUpdate`/fee-delegated variants when `from` is a `SmartContractAccountType` [8](#0-7) , so the impact there is mitigated by an independent, redundant check. However, for **externally owned accounts using a `RoleBased` key where one role slot has been deliberately locked with `AccountKeyFail`** (a legitimate and tested pattern per `tests/account_keytype_test.go:1421-1500`), the holder of the (still active) `RoleAccountUpdate` key can submit an ordinary `TxTypeAccountUpdate` that swaps that `Fail` slot to any other key type. `CheckReplacable` will take the type-mismatch branch and only run `CheckInstallable` on the new key, silently reinstating a role that was supposed to be permanently disabled — the exact "created with an allowed/restricted type, then updated to a disallowed type without the applicable check being re-run" pattern from the reference OpenShift CVE.

### Impact Explanation
This breaks an intended, documented invariant of the AccountKey system: that `AccountKeyFail` roles/keys are permanently non-functional/non-updatable. Any protocol, wallet, or user relying on `AccountKeyFail` to permanently revoke a role (e.g., irreversibly disabling `RoleFeePayer` or `RoleTransaction` for an address as a security/compliance control) can have that guarantee silently violated by a single `TxTypeAccountUpdate` transaction that targets a different key type for that slot. This is a state-integrity/authorization-invariant violation reachable by any account holder with control of the currently active `RoleAccountUpdate` key, undermining assumptions other contracts or off-chain systems may place on the permanence of a `Fail` key.

### Likelihood Explanation
High reachability: the bypass requires only a single, ordinary `TxTypeAccountUpdate` (or fee-delegated variant) transaction with a role-based key where the target slot's new key type differs from `AccountKeyTypeFail`. No special privileges, races, or forks are required, and the existing test suite already demonstrates the "install Fail into a role" half of the scenario succeeds; the "replace Fail with a different type" half follows directly from the same code path and is not covered by any negative test.

### Recommendation
Modify `CheckReplacable` (or its callers) so that when `oldKey` is `AccountKeyTypeFail` (or more generally, whenever the old key explicitly disallows any replacement), the type-mismatch branch still consults `oldKey.CheckUpdatable`/an old-key-side veto before falling through to `newKey.CheckInstallable`. E.g.:
```go
func CheckReplacable(oldKey AccountKey, newKey AccountKey, currentBlockNumber uint64) error {
	if oldKey.Type() == AccountKeyTypeFail {
		return kerrors.ErrAccountKeyFailNotUpdatable
	}
	if oldKey.Type() == newKey.Type() {
		return oldKey.CheckUpdatable(newKey, currentBlockNumber)
	}
	return newKey.CheckInstallable(currentBlockNumber)
}
```

### Proof of Concept
1. Create an EOA and update its key to `AccountKeyRoleBased{RoleTransaction: Public(k0), RoleAccountUpdate: Public(k1), RoleFeePayer: Fail}` (mirrors `tests/account_keytype_test.go` step 6 pattern, lines 1475–1500).
2. Craft a `TxTypeAccountUpdate` transaction with `TxValueKeyAccountKey` set to `AccountKeyRoleBased{RoleTransaction: Public(k0), RoleAccountUpdate: Public(k1), RoleFeePayer: Public(kAttacker)}`, signed with `k1` (the still-valid `RoleAccountUpdate` key).
3. Submit via `txpool.AddRemote` / include in a block: `accountkey.CheckReplacable(Fail, Public(kAttacker), ...)` takes the `oldKey.Type() != newKey.Type()` branch, calls only `Public(kAttacker).CheckInstallable()` (which succeeds), and the update is accepted — even though `AccountKeyFail.CheckUpdatable` would have unconditionally returned `ErrAccountKeyFailNotUpdatable`.
4. The account now has a live `RoleFeePayer` key controlled by the attacker, reversing the previously "permanent" lock, contradicting the code's own documentation and existing invariant tests.

### Citations

**File:** blockchain/types/accountkey/account_key_fail.go (L28-32)
```go
// AccountKeyFail is used to prevent smart contract accounts from withdrawing tokens
// from themselves with a public key recovery mechanism.
// Kaia assumes that the only way to take tokens from smart contract account is using
// `transfer()` in the smart contract code.
type AccountKeyFail struct{}
```

**File:** blockchain/types/accountkey/account_key_fail.go (L86-89)
```go
func (a *AccountKeyFail) CheckUpdatable(newKey AccountKey, currentBlockNumber uint64) error {
	// AccountKeyFail cannot be updated with any key, hence it returns always an error.
	return kerrors.ErrAccountKeyFailNotUpdatable
}
```

**File:** blockchain/types/accountkey/account_key.go (L123-129)
```go
// CheckReplacable returns nil if newKey can replace oldKey. The function checks updatability of newKey regardless of the newKey type.
func CheckReplacable(oldKey AccountKey, newKey AccountKey, currentBlockNumber uint64) error {
	if oldKey.Type() == newKey.Type() {
		return oldKey.CheckUpdatable(newKey, currentBlockNumber)
	}
	return newKey.CheckInstallable(currentBlockNumber)
}
```

**File:** blockchain/types/tx_internal_data_account_update.go (L292-296)
```go
func (t *TxInternalDataAccountUpdate) Validate(stateDB StateDB, currentBlockNumber uint64, onlyMutableChecks bool) error {
	oldKey := stateDB.GetKey(t.From)
	if err := accountkey.CheckReplacable(oldKey, t.Key, currentBlockNumber); err != nil {
		return err
	}
```

**File:** blockchain/types/tx_internal_data_fee_delegated_account_update.go (L354-358)
```go
func (t *TxInternalDataFeeDelegatedAccountUpdate) Validate(stateDB StateDB, currentBlockNumber uint64, onlyMutableChecks bool) error {
	oldKey := stateDB.GetKey(t.From)
	if err := accountkey.CheckReplacable(oldKey, t.Key, currentBlockNumber); err != nil {
		return err
	}
```

**File:** blockchain/types/tx_internal_data_fee_delegated_account_update_with_ratio.go (L379-383)
```go
func (t *TxInternalDataFeeDelegatedAccountUpdateWithRatio) Validate(stateDB StateDB, currentBlockNumber uint64, onlyMutableChecks bool) error {
	oldKey := stateDB.GetKey(t.From)
	if err := accountkey.CheckReplacable(oldKey, t.Key, currentBlockNumber); err != nil {
		return err
	}
```

**File:** blockchain/types/accountkey/account_key_role_based.go (L245-263)
```go
		for i := range lenNewKey {
			switch {
			// A composite key is not allowed.
			case (*newKey)[i].IsCompositeType():
				return kerrors.ErrNestedCompositeType
			// If newKey is longer than oldKey, init the new attributes.
			case i >= lenOldKey:
				if err := (*newKey)[i].CheckInstallable(currentBlockNumber); err != nil {
					return err
				}
			// Do nothing for AccountKeyTypeNil
			case (*newKey)[i].Type() == AccountKeyTypeNil:

			// Check whether the newKey is replacable or not
			default:
				if err := CheckReplacable((*a)[i], (*newKey)[i], currentBlockNumber); err != nil {
					return err
				}
			}
```

**File:** blockchain/types/tx_internal_data.go (L782-798)
```go
	// Group 2: From must be EOA without code
	case TxTypeAccountUpdate,
		TxTypeFeeDelegatedAccountUpdate,
		TxTypeFeeDelegatedAccountUpdateWithRatio:
		acc := stateDB.GetAccount(from)
		if acc == nil {
			return nil
		}
		if acc.Type() == account.SmartContractAccountType {
			return kerrors.ErrFromMustBeEOAWithoutCode
		}
		eoa, ok := acc.(*account.ExternallyOwnedAccount)
		if !ok || !bytes.Equal(eoa.GetCodeHash(), emptyCodeHash) {
			return kerrors.ErrFromMustBeEOAWithoutCode
		}

		return nil
```
