### Title
Unbounded Recursive RLP Decoding of Nested `AccountKeyRoleBased` Enables Stack-Overflow DoS via a Single AccountUpdate Transaction - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
The reported NestJS bug is a recursion-depth DoS: `handleData()` recurses once per valid sub-message with no depth cap, so a small crafted payload exhausts the call stack before any size/budget check can stop it. Kaia's `AccountKeyRoleBased.DecodeRLP` has the same structural weakness: it recursively decodes each role-key entry via `rlp.DecodeBytes`, and if a role-key entry is itself encoded as `AccountKeyTypeRoleBased`, decoding recurses into `AccountKeyRoleBased.DecodeRLP` again with no depth limitation, purely driven by attacker-controlled bytes.

### Finding Description
`AccountKeySerializer.DecodeRLP` reads a key type tag and then dispatches to the concrete `AccountKey` implementation's own `DecodeRLP`: [1](#0-0) 

When the key type is `AccountKeyTypeRoleBased`, `AccountKeyRoleBased.DecodeRLP` iterates over each encoded sub-key byte string and recursively decodes it via a fresh `AccountKeySerializer`: [2](#0-1) 

Nothing in this decode path checks whether a sub-key is itself a composite `RoleBased` type before recursing — that check (`ErrNestedCompositeType`) is only enforced later, in application-level validation (referenced in `blockchain/state_transition.go` and exercised in `tests/account_keytype_test.go`), which is invoked only *after* the full transaction (including the account key blob) has already been RLP-decoded off the wire: [3](#0-2) 

Because RLP encoding of a "wrapper" `RoleBased(RoleBased(RoleBased(...)))` chain is extremely compact (each nesting level only adds a few bytes of list/type overhead), an attacker can encode thousands of nesting levels in a payload well under normal transaction-size limits. Since `Transaction.DecodeRLP`/`TxInternalDataAccountUpdate` decoding is performed unconditionally as part of ingesting any submitted raw transaction (e.g., via `eth_sendRawTransaction` or p2p tx propagation into the pool), this recursive decode executes before the `ErrNestedCompositeType` guard has any chance to run, mirroring the NestJS class of bug: a legitimate-looking, budget-bypassing recursive parse that blows the stack instead of hitting an intended limit.

### Impact Explanation
A successful exploit causes the Go runtime to hit its stack-growth limit inside the RLP decoding routine, resulting in a fatal, unrecoverable runtime crash (`fatal error: stack overflow`) of the node process handling the transaction — this is not a catchable Go panic and cannot be recovered by `defer/recover`, so it crashes the entire node (RPC server, txpool, block production/validation) that decodes the transaction. Because RLP-decoded raw transactions are processed by every txpool-admitting node (validators, RPC nodes) that receives or is asked to decode this data, this can be leveraged as a denial-of-service against the network's availability, matching the CVSS Availability:High rating of the source advisory.

### Likelihood Explanation
Any unprivileged party can construct this payload offline (no chain state, keys, or gas is required to build the bytes) and only needs to get it decoded — submitting it as a `TxTypeAccountUpdate`/`TxTypeFeeDelegatedAccountUpdate` transaction via a public RPC endpoint (`eth_sendRawTransaction`) is sufficient to reach `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP`. No authentication, staking, or governance privilege is required, making likelihood high once the nesting-depth threshold needed to overflow the stack is confirmed empirically.

### Recommendation
Enforce a maximum recursion/nesting depth check inside `AccountKeyRoleBased.DecodeRLP` (and generally in `AccountKeySerializer.DecodeRLP`) before recursing into a nested key, rejecting any `AccountKeyTypeRoleBased`-within-`AccountKeyTypeRoleBased` structure immediately at decode time rather than deferring the check to post-decode validation. This aligns the fix with the `ErrNestedCompositeType` intent already present in the codebase, but moves the enforcement to the point where recursion actually occurs.

### Proof of Concept
1. Construct an `AccountKeySerializer`-encoded blob `K0` of type `AccountKeyNil` (or any minimal leaf key).
2. Wrap `K0` as `AccountKeyRoleBased([K0])` and RLP-encode to get `K1`.
3. Repeat wrapping `K1` as `AccountKeyRoleBased([K1])` → `K2`, and so on for N (e.g., tens of thousands) iterations, each adding only a few bytes.
4. Set this deeply nested blob as the `TxValueKeyAccountKey` of a `TxTypeAccountUpdate` transaction, sign it, RLP-encode the full transaction, and submit it via `eth_sendRawTransaction` (or directly to `txpool.AddRemote`).
5. Decoding the transaction triggers `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` recursively N times before any nested-type validation occurs, crashing the process with a Go stack overflow once N is large enough.

Note: I could not execute this PoC to empirically confirm the exact N required to overflow Kaia's default goroutine stack limit; this would need to be verified in a running environment.

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

**File:** tests/account_keytype_test.go (L1793-1816)
```go
	txpool := blockchain.NewTxPool(blockchain.DefaultTxPoolConfig, bcdata.bc.Config(), bcdata.bc, bcdata.govModule)

	// 2. Update an accountKey with a nested RoleBasedKey.
	{
		values := map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      anon.Nonce,
			types.TxValueKeyFrom:       anon.Addr,
			types.TxValueKeyGasLimit:   gasLimit,
			types.TxValueKeyGasPrice:   gasPrice,
			types.TxValueKeyAccountKey: nestedAccKey,
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeAccountUpdate, values)
		assert.Equal(t, nil, err)

		err = tx.SignWithKeys(signer, []*ecdsa.PrivateKey{roleKey.Keys[accountkey.RoleAccountUpdate]})
		assert.Equal(t, nil, err)

		// For tx pool validation test
		{
			err = txpool.AddRemote(tx)
			assert.Equal(t, kerrors.ErrNestedCompositeType, err)
		}

```
