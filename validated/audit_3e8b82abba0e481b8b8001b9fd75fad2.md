### Title
Unbounded recursive RLP decoding of self-referential `AccountKeyRoleBased` allows attacker-controlled stack-overflow DoS - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively decodes nested `AccountKeySerializer` values without any depth or nesting limit, mirroring the self-referential-schema recursion bug class described in CVE-2026-59645 (Bouncy Castle OER parser). A single, ordinary `AccountUpdate` / `FeeDelegatedAccountUpdate*` transaction submitted by any unprivileged sender (e.g. via `eth_sendRawTransaction`) can encode an `AccountKeyRoleBased` value whose sub-keys are themselves `AccountKeyRoleBased` values, nested arbitrarily deep. RLP decoding happens before any composite-type validation, so the recursion depth is bounded only by the outer transaction size limit, not by any explicit guard.

### Finding Description
The account key transport format wraps a type tag and RLP-encoded key bytes in `AccountKeySerializer`: [1](#0-0) 

When the type is `AccountKeyTypeRoleBased`, `NewAccountKey` returns an `*AccountKeyRoleBased`, and `s.Decode(serializer.key)` dispatches into `AccountKeyRoleBased.DecodeRLP`: [2](#0-1) 

This method decodes a list of byte strings and, for **each** element, calls `rlp.DecodeBytes(b, &serializer)` on a fresh `AccountKeySerializer` — which can again resolve to `AccountKeyTypeRoleBased`, recursively re-entering `AccountKeyRoleBased.DecodeRLP`. There is no depth counter or nesting limit anywhere in the `rlp` package (`rlp/decode.go`) or in the `accountkey` package to bound this recursion.

The only defense against nested composite keys — `CheckInstallable`, which explicitly rejects `AccountKeyTypeRoleBased` nested inside another `AccountKeyRoleBased` (`kerrors.ErrNestedCompositeType`) — runs strictly *after* the full RLP decode has already completed: [3](#0-2) 

`TxInternalDataAccountUpdate.DecodeRLP` performs the vulnerable decode step first, and only afterward would any validation (`Validate` → `accountkey.CheckReplacable` → `CheckInstallable`) run: [4](#0-3) [5](#0-4) 

Because each nesting level only needs to wrap the inner encoded key bytes in one more `AccountKeySerializer`/`AccountKeyRoleBased` layer, the size overhead per recursion level is small and roughly linear, so a large recursion depth (thousands of levels) is achievable within ordinary transaction size limits, driving unbounded Go call-stack growth during `rlp.DecodeBytes`/`s.Decode` — a classic stack-overflow DoS, directly analogous to the referenced OER self-referential schema recursion issue.

### Impact Explanation
An unprivileged transaction sender can craft a single `AccountUpdate` (or `FeeDelegatedAccountUpdate`/`WithRatio`) transaction containing a deeply self-nested `AccountKeyRoleBased` value and submit it via public RPC (`eth_sendRawTransaction`) or propagate it to the transaction pool. Decoding this transaction (in the tx pool intake path, block processing, or JSON/RPC decoding paths that call `rlp.DecodeBytes`) triggers unbounded recursion in `AccountKeyRoleBased.DecodeRLP`, which can exhaust the goroutine stack and crash the node process (Go runtime fatal stack overflow is not recoverable via `panic`/`recover`). This is a node-crashing denial-of-service reachable from a single external transaction, affecting availability of transaction processing and RPC nodes — consistent with a Medium/High severity availability-only advisory analogous to CVE-2026-59645 (CVSS vector emphasizing `VA:H`).

### Likelihood Explanation
High likelihood: the attack requires only crafting and signing (or even submitting unsigned/malformed, since decode happens before signature verification in some paths) a standard `AccountUpdate`-family transaction with a nested `AccountKeyRoleBased` payload, then submitting it through the standard public transaction submission path. No special privileges, validator status, or state preconditions are required — this is reachable by any account able to send a transaction or call the JSON-RPC endpoint.

### Recommendation
Add an explicit recursion/nesting depth limit when decoding `AccountKeySerializer`/`AccountKeyRoleBased` (e.g., track and pass a depth counter through `DecodeRLP`, or reject/short-circuit decoding of `AccountKeyTypeRoleBased` sub-keys within `AccountKeyRoleBased.DecodeRLP` immediately during decode, rather than deferring the "nested composite" rejection to `CheckInstallable`, which runs only after the entire recursive decode has completed). Enforcing the "no composite key inside a composite key" rule at decode time (not just at installation time) closes the recursion entirely, since `AccountKeyRoleBased` is the only composite/self-referential account key type.

### Proof of Concept
Conceptual construction (illustrates the recursive structure; exact byte-level encoding follows `AccountKeySerializer`/`AccountKeyRoleBased` RLP rules in [2](#0-1)  and [1](#0-0) ):

```go
// Build N nested AccountKeyRoleBased layers, each containing exactly one
// nested AccountKeyRoleBased element, terminating with a leaf AccountKeyPublic.
func buildNestedRoleBasedKey(depth int) accountkey.AccountKey {
    leaf := accountkey.NewAccountKeyPublicWithValue(somePubKey)
    key := leaf
    for i := 0; i < depth; i++ {
        key = accountkey.NewAccountKeyRoleBasedWithValues([]accountkey.AccountKey{key})
    }
    return key
}

// Embed into an AccountUpdate tx, sign it (or send raw), and submit via
// eth_sendRawTransaction or txpool.AddRemote. Decoding the resulting
// transaction bytes recurses `depth` times inside AccountKeyRoleBased.DecodeRLP,
// with `depth` chosen large enough (e.g., several thousand) to overflow the
// goroutine stack while staying within normal tx size limits.
```

Note: I was unable to execute this PoC or measure the exact transaction-size-to-recursion-depth ratio needed to trigger a Go stack overflow, since I only have read access to the codebase index (no runtime/terminal access). The recursive code path and the absence of any depth guard in both the `rlp` package and the `accountkey` decode logic were confirmed directly from source, per the citations above.

### Citations

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

**File:** blockchain/types/tx_internal_data_account_update.go (L163-175)
```go
func (t *TxInternalDataAccountUpdate) DecodeRLP(s *rlp.Stream) error {
	dec := newTxInternalDataAccountUpdateSerializable()

	if err := s.Decode(dec); err != nil {
		return err
	}
	if err := t.fromSerializable(dec); err != nil {
		logger.Warn("DecodeRLP failed", "err", err)
		return kerrors.ErrUnserializableKey
	}

	return nil
}
```

**File:** blockchain/types/tx_internal_data_account_update.go (L292-301)
```go
func (t *TxInternalDataAccountUpdate) Validate(stateDB StateDB, currentBlockNumber uint64, onlyMutableChecks bool) error {
	oldKey := stateDB.GetKey(t.From)
	if err := accountkey.CheckReplacable(oldKey, t.Key, currentBlockNumber); err != nil {
		return err
	}
	if err := validate7702(stateDB, t.Type(), t.From, common.Address{}); err != nil {
		return err
	}
	return nil
}
```
