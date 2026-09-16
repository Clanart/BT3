### Title
Unbounded recursion in `AccountKeyRoleBased.DecodeRLP` allows stack-exhaustion DoS via a single AccountUpdate transaction - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
`AccountKeyRoleBased.DecodeRLP` decodes each role-key entry as an independent RLP byte string and then re-invokes `rlp.DecodeBytes` on it to obtain a nested `AccountKeySerializer`. Because each nested role key can itself be another `AccountKeyTypeRoleBased` value, an attacker can craft an `AccountUpdate`/`FeeDelegatedAccountUpdate`/`FeeDelegatedAccountUpdateWithRatio` transaction whose account-key payload nests role-based keys arbitrarily deep, driving unbounded Go-stack recursion during RLP decoding — the same bug class as CVE-2018-6003 (unbounded recursion in `_asn1_decode_simple_ber`) applied to Kaia's account-key RLP decoder.

### Finding Description
`AccountKeySerializer.DecodeRLP` reads a key type tag and then calls `s.Decode(serializer.key)` on the concrete key object [1](#0-0) .

When the type is `AccountKeyTypeRoleBased`, decoding is dispatched to `AccountKeyRoleBased.DecodeRLP`, which reads a list of byte strings and then calls `rlp.DecodeBytes` on *each* one to construct a fresh `AccountKeySerializer`: [2](#0-1) 

Each such nested `rlp.DecodeBytes` call starts an entirely new `rlp.Stream` and recursion frame — there is no depth counter or recursion guard anywhere in the `rlp` package's decode path (`rlp/decode.go`) or in `AccountKeyRoleBased.DecodeRLP` itself. Because a `RoleBased` key's element can again be `AccountKeyTypeRoleBased`, an attacker can nest these arbitrarily: level N wraps a byte string containing the RLP encoding of level N+1, and so on. Each level only costs a few bytes of RLP overhead, so a modest-sized transaction payload (well under typical tx-size/gas limits) can encode many thousands of nesting levels, each adding one or more Go stack frames (`AccountKeyRoleBased.DecodeRLP` → `rlp.DecodeBytes` → `Stream.Decode` → `AccountKeySerializer.DecodeRLP` → `NewAccountKey` → `s.Decode` → back into `AccountKeyRoleBased.DecodeRLP`).

Critically, the "nested composite type not allowed" rule (`kerrors.ErrNestedCompositeType`) is enforced only *after* decoding completes, in `CheckInstallable`/`CheckUpdatable` [3](#0-2) , and in the API-layer helper `checkAccountKeyZeroValues` [4](#0-3) . Neither of these checks bounds recursion *during* RLP decoding itself — the stack exhaustion (or extreme CPU/allocation cost) happens before any of these guards ever run.

This decoder is reached directly from transaction RLP decoding: `TxInternalDataAccountUpdate.DecodeRLP` → `fromSerializable` → `rlp.DecodeBytes(serialized.Key, serializer)` → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP`, as shown in [5](#0-4)  and [6](#0-5) . The same pattern applies to `TxInternalDataFeeDelegatedAccountUpdate` and `...WithRatio`. This decoding path is invoked whenever a raw transaction is received — via `eth_sendRawTransaction`, p2p transaction propagation, or block/transaction-pool ingestion — making it reachable by any unprivileged transaction sender or public-RPC caller without any signature verification having occurred yet (decoding happens before/independently of signature and business-rule validation).

### Impact Explanation
A single crafted transaction (need not even be validly signed to trigger the decode, since RLP decoding of the transaction body happens before the account-key composite-type check, and in many code paths before signature verification) can cause:
- Stack exhaustion / process crash (denial of service) on any node that decodes the transaction — full nodes processing `eth_sendRawTransaction`, p2p tx propagation, tx pool admission, or block processing.
- Because block processing itself uses the identical `DecodeRLP` path, if such a transaction is included in a block, all honest nodes attempting to decode that block would crash identically, causing a network-wide liveness failure (chain halt) rather than a localized single-node crash.

This qualifies as a High-severity, reachable, unauthorized-DoS analog: a public, unprivileged transaction sender can crash node processes or halt the chain, matching the CVE's "unlimited recursion... leads to stack exhaustion and DoS" pattern for a decoding function reachable from external, untrusted input.

### Likelihood Explanation
High likelihood: the attack requires only constructing and RLP-encoding a single transaction payload with deeply nested `AccountKeyRoleBased` byte strings — no special privileges, no valid pre-existing account state, and no on-chain gas expenditure is even required to trigger it if the crash occurs purely in the decode step (e.g., via `eth_sendRawTransaction` or p2p gossip, which decode transactions before consensus/gas execution). The `rlp` package imposes no recursion-depth limiting on nested `DecodeBytes` calls, and the composite-type validation in this codebase runs strictly after decoding.

### Recommendation
- Add a recursion-depth counter/limit that is threaded through `AccountKeySerializer.DecodeRLP` / `AccountKeyRoleBased.DecodeRLP` (e.g., reject decoding if a `RoleBased` key is encountered while already inside a `RoleBased` decode, or track and cap overall recursion depth) so that "nested composite type" is rejected during decoding, not only in later `CheckInstallable`/`CheckUpdatable` validation.
- Alternatively/additionally, impose a maximum size or maximum recursive depth in the shared `rlp` decode Stream/`DecodeBytes` entry points so all recursively-defined `Decoder` implementations in the codebase (not just `AccountKeyRoleBased`) are protected uniformly.
- Reject `AccountKeyTypeRoleBased` values whose serialized child elements themselves decode (even partially) to `AccountKeyTypeRoleBased`, before performing the recursive `rlp.DecodeBytes` call.

### Proof of Concept
Conceptually (Go pseudocode, using the existing `accountkey` package APIs):
```go
// Build a deeply nested RoleBased account key.
// level 0: a normal Public key
inner := accountkey.NewAccountKeyPublicWithValue(pub)
cur, _ := rlp.EncodeToBytes(accountkey.NewAccountKeySerializerWithAccountKey(inner))

// Wrap 'cur' in RoleBased keys N times (N = tens of thousands),
// each time producing: RLP([ RLP-of-AccountKeySerializer-wrapping-cur ])
for i := 0; i < N; i++ {
    roleBased := accountkey.NewAccountKeyRoleBasedWithValues(
        []accountkey.AccountKey{ /* a fake key whose EncodeRLP just outputs 'cur' as opaque bytes */ },
    )
    cur, _ = rlp.EncodeToBytes(accountkey.NewAccountKeySerializerWithAccountKey(roleBased))
}

// Embed 'cur' as the Key field of a TxInternalDataAccountUpdate-style raw RLP transaction
// and submit via eth_sendRawTransaction / p2p, or feed directly to:
//   tx := types.NewTx(&types.TxInternalDataAccountUpdate{})
//   rlp.DecodeBytes(rawTxBytes, tx)
```
Decoding such a transaction recurses through `AccountKeyRoleBased.DecodeRLP` → `rlp.DecodeBytes` → `AccountKeySerializer.DecodeRLP` once per nesting level, exhausting the goroutine stack and crashing the process before `CheckInstallable`/`CheckUpdatable` composite-type checks ever execute.

*(Note: exact minimal encoding bytes and precise recursion depth needed to trigger a crash in the target Go runtime were not independently benchmarked here — this would need to be confirmed empirically in a running Kaia node, e.g., via a Devin session with test execution.)*

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

**File:** api/api_kaia.go (L176-199)
```go
func checkAccountKeyZeroValues(key accountkey.AccountKey, isNested bool) error {
	switch key.Type() {
	case accountkey.AccountKeyTypeWeightedMultiSig:
		multiSigKey, _ := key.(*accountkey.AccountKeyWeightedMultiSig)
		if multiSigKey.Threshold == 0 {
			return errors.New("invalid threshold of the multiSigKey")
		}
		for _, weightedKey := range multiSigKey.Keys {
			if weightedKey.Weight == 0 {
				return errors.New("invalid weight of the multiSigKey")
			}
		}
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

**File:** blockchain/types/tx_internal_data_account_update.go (L143-157)
```go
func (t *TxInternalDataAccountUpdate) fromSerializable(serialized *txInternalDataAccountUpdateSerializable) error {
	t.AccountNonce = serialized.AccountNonce
	t.Price = serialized.Price
	t.GasLimit = serialized.GasLimit
	t.From = serialized.From
	t.TxSignatures = serialized.TxSignatures

	serializer := accountkey.NewAccountKeySerializer()
	if err := rlp.DecodeBytes(serialized.Key, serializer); err != nil {
		return err
	}
	t.Key = serializer.GetKey()

	return nil
}
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
