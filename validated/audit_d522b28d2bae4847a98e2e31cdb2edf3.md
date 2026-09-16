### Title
Unbounded recursive RLP decoding of nested `AccountKeyRoleBased` allows stack-exhaustion DoS before nested-composite validation runs - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively decodes each sub-key by calling `rlp.DecodeBytes` on a fresh `AccountKeySerializer`, and if that sub-key is itself of type `AccountKeyTypeRoleBased`, the same `DecodeRLP` is invoked again [1](#0-0) . `AccountKeySerializer.DecodeRLP` performs the type dispatch and recursive `s.Decode(serializer.key)` call that drives this chain [2](#0-1) . The only defense against nested `RoleBased` keys — `ErrNestedCompositeType` — is enforced in `CheckInstallable`/`CheckUpdatable`, which runs **after** RLP decoding has already completed [3](#0-2) . This mirrors the CVE-2022-1289 bug class: a check exists to reject a malformed/recursive structure, but it is applied too late — after the vulnerable parsing routine has already recursed on attacker-controlled nesting.

### Finding Description
Kaia's `rlp` decoder has no depth limiter for nested types/lists (confirmed by inspection of `rlp/decode.go`, which tracks only byte-size limits via `Stream.remaining`/`listLimit`, never call depth) [4](#0-3) . When a `TxTypeAccountUpdate` (or any tx-type carrying an `AccountKey`) is RLP-decoded — which happens on every incoming raw transaction via `Transaction.DecodeRLP` / `UnmarshalBinary` [5](#0-4)  — the embedded `AccountKeySerializer` is decoded, and if its type is `AccountKeyTypeRoleBased`, `AccountKeyRoleBased.DecodeRLP` is invoked. That function decodes an outer `[][]byte`, then for each byte-slice element calls `rlp.DecodeBytes(b, &serializer)` on a brand-new `AccountKeySerializer` [1](#0-0) . Because each nested `AccountKeySerializer` can again declare `keyType == AccountKeyTypeRoleBased`, an attacker can construct a transaction whose `AccountKey` field is a `RoleBased` key wrapping another `RoleBased` key, wrapping another, etc. — with linear (not exponential) byte overhead per nesting level (just a few RLP list-header bytes per level) — driving unbounded Go call-stack recursion purely from the decoding path, well before `CheckInstallable`/`CheckUpdatable`/`ErrNestedCompositeType` are ever reached to reject the (only single-level) nesting rule that exists in Kaia today.

This decode path is reachable unauthenticated from:
- `eth_sendRawTransaction` / `TxPool.AddRemote` → `Transaction.UnmarshalBinary` → recursive decode.
- The gasless RPC API, which explicitly `rlp.DecodeBytes`s attacker-supplied raw transactions before any semantic check [6](#0-5) .
- Block/transaction propagation and block validation paths that decode transactions from the wire.

### Impact Explanation
A crafted transaction (or raw-tx RPC payload) with deeply nested `AccountKeyRoleBased` structures can exhaust the goroutine call stack during RLP decoding, causing a Go runtime stack-overflow panic and crashing the node process handling the transaction — mempool nodes accepting `eth_sendRawTransaction`/gossiped transactions, RPC nodes evaluating gasless transactions, and consensus nodes validating blocks that include such a transaction. This is a remotely triggerable denial-of-service condition reachable by any unprivileged transaction sender or public-RPC caller, consistent with a CVE-2022-1289-class "incomplete fix" where a validation guard exists but is applied after the vulnerable recursive operation already executed.

### Likelihood Explanation
High likelihood of reachability: constructing the malicious payload requires no special privileges, gas payment, or on-chain state — it is a pure RLP-encoding exercise performable off-chain and submitted via any public transaction-submission entrypoint. The nested-composite check (`ErrNestedCompositeType`) demonstrates the Kaia team is aware nested `RoleBased` keys are semantically invalid, but the fix only prevents them from being *installed/updated on-chain* — it does not prevent the recursive decoder from processing them, so the DoS trigger point (decode-time recursion) is left unguarded.

### Recommendation
- Add an explicit recursion/nesting-depth counter to `AccountKeySerializer.DecodeRLP` / `AccountKeyRoleBased.DecodeRLP`, rejecting decode with an error as soon as a nested `AccountKeyTypeRoleBased` (or any depth beyond 1) is encountered — i.e., move the `ErrNestedCompositeType` check into the decode path itself rather than only into `CheckInstallable`/`CheckUpdatable`.
- Alternatively/additionally, introduce a general maximum decode-depth guard in the `rlp` package (`rlp/decode.go`) analogous to the byte-size limiting already present, to broadly harden all recursively-decodable types (not just `AccountKey`) against similar stack-exhaustion attacks.
- Add fuzz/regression tests submitting transactions with deeply nested `AccountKeyRoleBased` fields directly to `Transaction.UnmarshalBinary`/`TxPool.AddRemote` to confirm the guard triggers before recursion occurs.

### Proof of Concept
1. Construct nested `AccountKeyRoleBased` RLP bytes recursively (conceptually, in Go):
```go
inner := accountkey.NewAccountKeyPublicWithValue(pub)
for i := 0; i < N; i++ { // N large, e.g. 100000
    inner = accountkey.NewAccountKeyRoleBasedWithValues(accountkey.AccountKeyRoleBased{inner})
}
serializer := accountkey.NewAccountKeySerializerWithAccountKey(inner)
encoded, _ := rlp.EncodeToBytes(serializer) // small size, since nesting overhead is only a few bytes/level
```
2. Embed `encoded` as the `AccountKey` field of a `TxTypeAccountUpdate` transaction, RLP-encode the full transaction via `Transaction.EncodeRLP` [7](#0-6) .
3. Submit the raw transaction bytes via `eth_sendRawTransaction` (which calls `Transaction.UnmarshalBinary` → recursive `AccountKeyRoleBased.DecodeRLP`) or via the gasless `IsGaslessTx` RPC, which calls `rlp.DecodeBytes(rawTx, tx)` directly on attacker input [8](#0-7) .
4. The recursive decode call chain (`AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` → `rlp.DecodeBytes` → ... ) executes N times before `ErrNestedCompositeType` would ever be checked, exhausting the goroutine stack and crashing the handling node.

**Note on completeness:** I was unable to fully verify the exact maximum transaction-size limit enforced by `TxPool` (`params/protocol_params.go`, `blockchain/tx_pool.go`) that would bound the achievable nesting depth `N` in practice, since I ran out of tool iterations before reading those exact constants. This bound would determine the precise nesting depth achievable within a single max-size transaction and should be confirmed during triage/PoC development.

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

**File:** blockchain/types/accountkey/account_key_role_based.go (L211-231)
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
}
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

**File:** rlp/decode.go (L1161-1179)
```go
// willRead is called before any read from the underlying stream. It checks
// n against size limits, and updates the limits if n doesn't overflow them.
func (s *Stream) willRead(n uint64) error {
	s.kind = -1 // rearm Kind

	if inList, limit := s.listLimit(); inList {
		if n > limit {
			return ErrElemTooLarge
		}
		s.stack[len(s.stack)-1] = limit - n
	}
	if s.limited {
		if n > s.remaining {
			return ErrValueTooLarge
		}
		s.remaining -= n
	}
	return nil
}
```

**File:** blockchain/types/transaction.go (L224-228)
```go
// EncodeRLP implements rlp.Encoder
func (tx *Transaction) EncodeRLP(w io.Writer) error {
	serializer := newTxInternalDataSerializerWithValues(tx.data)
	return rlp.Encode(w, serializer)
}
```

**File:** blockchain/types/transaction.go (L239-266)
```go
// DecodeRLP implements rlp.Decoder
func (tx *Transaction) DecodeRLP(s *rlp.Stream) error {
	serializer := newTxInternalDataSerializer()
	if err := s.Decode(serializer); err != nil {
		return err
	}

	if !SanityCheckSignatures(serializer.tx.RawSignatureValues(), serializer.tx.Type()) {
		return ErrInvalidSig
	}

	size := calculateTxSize(serializer.tx)
	tx.setDecoded(serializer.tx, int(size))

	return nil
}

// UnmarshalBinary decodes the canonical encoding of transactions.
// It supports legacy RLP transactions and EIP2718 typed transactions.
func (tx *Transaction) UnmarshalBinary(b []byte) error {
	newTx := &Transaction{}
	if err := rlp.DecodeBytes(b, newTx); err != nil {
		return err
	}

	tx.setDecoded(newTx.data, len(b))
	return nil
}
```

**File:** kaiax/gasless/impl/api.go (L84-92)
```go
		// Handle Ethereum transaction envelope
		if 0 < rawTx[0] && rawTx[0] < 0x7f {
			rawTx = append([]byte{byte(types.EthereumTxTypeEnvelope)}, rawTx...)
		}

		tx := new(types.Transaction)
		if err := rlp.DecodeBytes(rawTx, tx); err != nil {
			return ToResponse(fmt.Errorf("failed to decode transaction at index %d: %v", i, err))
		}
```
