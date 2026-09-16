### Title
Unbounded recursive decoding of nested `AccountKeyRoleBased` allows memory-exhaustion DoS via AccountUpdate transaction - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
CVE-2016-5011 describes `parse_dos_extended` in `libblkid` following a chain of extended partition records with no depth/loop bound, letting an attacker force unbounded memory consumption from a single crafted structure. The same bug class — recursive/self-referential structure decoding with no depth limit, where the "is this nesting allowed" check happens only *after* the expensive parsing has already completed — exists in Kaia's `AccountKeyRoleBased.DecodeRLP`.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a list of opaque byte blobs and, for each one, recursively invokes `AccountKeySerializer.DecodeRLP`, which in turn calls `NewAccountKey` and then `s.Decode(serializer.key)`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` itself just dispatches based on the decoded key type and recursively decodes into whatever `AccountKey` implementation that type maps to: [2](#0-1) 

Nothing in this decode path enforces a recursion depth limit or rejects a role-key entry that is itself an `AccountKeyTypeRoleBased`. The "no nested composite key" invariant is only enforced later, in `CheckInstallable`/`CheckUpdatable`: [3](#0-2) 

and this rule is explicitly documented and tested as being about *installation/update*, not decoding: [4](#0-3) 

Because the rejection is applied after decoding, an attacker can craft an RLP blob encoding `AccountKeyRoleBased` whose entries are themselves `AccountKeyRoleBased` blobs, nested many levels deep (each level fanning out to up to `RoleLast` sub-entries). Each level triggers a fresh `rlp.DecodeBytes` call and allocates a new `[]AccountKey` slice, so decode cost/memory grows multiplicatively with nesting depth and fan-out before the `CheckInstallable`/`CheckUpdatable` rejection is ever reached — directly analogous to the unbounded extended-partition chain traversal in the CVE.

### Impact Explanation
An attacker who can submit an `AccountUpdate`-type transaction (or any RPC path that calls `DecodeAccountKey`/`EncodeRLP`/`DecodeRLP` on attacker-supplied account key bytes, e.g. `KaiaAPI.DecodeAccountKey`) can force nodes that receive or process this transaction/blob to perform excessive, unbounded recursive allocation and CPU work purely during decoding, before any gas-metered EVM/tx-validation logic or the `ErrNestedCompositeType` check runs. This can degrade or crash public RPC nodes and full nodes propagating the transaction, a denial-of-service condition consistent with the CVE's "memory consumption" class and reachable by any unprivileged transaction sender or public-RPC caller.

### Likelihood Explanation
Reachable with a single crafted transaction or RPC call containing nested `AccountKeyRoleBased` RLP bytes — no special privilege, validator status, or peer position is required, matching the "unprivileged transaction sender" scope. Likelihood is Medium: exploitation requires only RLP-encoding skill, but actual DoS severity depends on how deep/large a payload can be constructed within the pre-decode transaction/RPC size limits (which I could not fully verify from available context — e.g. whether an outer size cap on the raw tx bytes bounds the achievable nesting/fan-out product).

### Recommendation
Enforce depth/complexity limits during `AccountKeyRoleBased.DecodeRLP` (and generally in `AccountKeySerializer.DecodeRLP`) rather than only at `CheckInstallable`/`CheckUpdatable` time — e.g., reject decoding as soon as a nested key's type is `AccountKeyTypeRoleBased`, or pass down and check a recursion-depth counter, so the `ErrNestedCompositeType`-equivalent rejection happens before recursive allocation, not after.

### Proof of Concept
Conceptual (I could not execute code in this environment, so this is a description, not a verified reproduction):
1. Construct an `AccountKeyRoleBased` RLP value whose role entries are themselves RLP-encoded `AccountKeySerializer` blobs of type `AccountKeyTypeRoleBased`, nested N levels deep, each level containing multiple such nested blobs (bounded only by `RoleLast` per level in the outer check, but that check is not applied during decode).
2. Submit this as the `AccountKey` field of a `TxTypeAccountUpdate` transaction, or pass it directly to `KaiaAPI.DecodeAccountKey`.
3. Observe that `rlp.DecodeBytes` recursion via `AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` performs allocation/work proportional to the full nested structure before `CheckInstallable`/`CheckUpdatable` ever runs and returns `kerrors.ErrNestedCompositeType`. [1](#0-0) [2](#0-1)

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

**File:** blockchain/types/accountkey/account_key_role_based.go (L211-230)
```go
func (a *AccountKeyRoleBased) CheckInstallable(currentBlockNumber uint64) error {
	// A zero-role key is not allowed.
	if len(*a) == 0 {
		return kerrors.ErrZeroLength
	}
	// Do not allow undefined roles.
	if len(*a) > (int)(RoleLast) {
		return kerrors.ErrLengthTooLong
	}
	for i := 0; i < len(*a); i++ {
		// A composite key is not allowed.
		if (*a)[i].IsCompositeType() {
			return kerrors.ErrNestedCompositeType
		}
		// If any key in the role cannot be initialized, return an error.
		if err := (*a)[i].CheckInstallable(currentBlockNumber); err != nil {
			return err
		}
	}
	return nil
```

**File:** blockchain/types/accountkey/account_key_serializer.go (L61-73)
```go
func (serializer *AccountKeySerializer) DecodeRLP(s *rlp.Stream) error {
	if err := s.Decode(&serializer.keyType); err != nil {
		return err
	}

	var err error
	serializer.key, err = NewAccountKey(serializer.keyType)
	if err != nil {
		return err
	}

	return s.Decode(serializer.key)
}
```

**File:** tests/account_keytype_test.go (L1697-1701)
```go
// TestAccountUpdateRoleBasedKeyNested tests account update with a nested RoleBasedKey.
// Nested RoleBasedKey is not allowed in Kaia.
// 1. Create an account with a RoleBasedKey.
// 2. Update an accountKey with a nested RoleBasedKey
func TestAccountUpdateRoleBasedKeyNested(t *testing.T) {
```
