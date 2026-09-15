This confirms the recursion path with no depth check during `DecodeRLP`, and validation (`CheckInstallable`/`CheckUpdatable`) only rejects nested composite types **after** the entire recursive decode has already completed. The recursion is genuinely unbounded at decode time: `AccountKeyRoleBased.DecodeRLP` [1](#0-0)  calls `rlp.DecodeBytes` on each nested element through `AccountKeySerializer.DecodeRLP` [2](#0-1) , which itself calls `NewAccountKey` [3](#0-2)  and then `s.Decode(serializer.key)` — if the inner key type is again `AccountKeyTypeRoleBased`, this re-enters `AccountKeyRoleBased.DecodeRLP` recursively with no depth counter. The nesting rejection only happens later in `CheckInstallable`/`CheckUpdatable` [4](#0-3) , i.e., after decoding is already complete.

### Title
Uncontrolled Recursion in `AccountKeyRoleBased.DecodeRLP` via Deeply Nested Role-Based Account Keys Enables Stack-Exhaustion DoS - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
An unprivileged transaction sender can submit an `AccountUpdate` (or fee-delegated equivalent) transaction, or any RPC call that decodes an `AccountKey` blob (e.g. `kaia_decodeAccountKey`), whose `Key` field contains an `AccountKeyRoleBased` value with many levels of self-nested `AccountKeyRoleBased` entries. Because `AccountKeyRoleBased.DecodeRLP` recursively calls back into `AccountKeySerializer.DecodeRLP` → `NewAccountKey` → `AccountKeyRoleBased.DecodeRLP` for each nested entry with no depth limit, a maliciously crafted key blob can drive the decoder into deep, unbounded recursion, analogous to the Squid `X-Forwarded-For` uncontrolled-recursion bug (CVE-2023-50269), which caused stack exhaustion from a single crafted, attacker-controlled recursive input.

### Finding Description
`AccountKeyRoleBased` is encoded as a list of RLP byte-strings, each of which is itself a full `AccountKeySerializer`-encoded `AccountKey` blob [5](#0-4) . During decode:
1. `AccountKeyRoleBased.DecodeRLP` reads the outer list into `[][]byte`.
2. For each element `b`, it calls `rlp.DecodeBytes(b, &serializer)` on a fresh `AccountKeySerializer`.
3. `AccountKeySerializer.DecodeRLP` decodes the `keyType` byte, calls `NewAccountKey(serializer.keyType)`, then `s.Decode(serializer.key)` [2](#0-1) .
4. If `keyType == AccountKeyTypeRoleBased`, `NewAccountKey` returns another `*AccountKeyRoleBased` [3](#0-2) , whose `DecodeRLP` is invoked again — recursing back to step 1.

There is no depth counter or nesting check anywhere in this call chain. The only place nested `RoleBased` keys are rejected is in `CheckInstallable`/`CheckUpdatable`, which run only *after* the entire (potentially very deep) structure has already been fully decoded [4](#0-3) . The existing regression test `TestAccountUpdateRoleBasedKeyNested` only exercises a single level of nesting built via the normal (non-adversarial) constructor path and asserts `ErrNestedCompositeType` is returned by post-decode validation — it does not test deep/adversarial RLP-crafted nesting depth [6](#0-5) .

Each nesting level only costs a handful of RLP bytes (list header + `keyType` byte + inner list wrapper), so an attacker can achieve very large recursion depth (thousands of levels) within an ordinary transaction payload size, well within typical tx size/gas limits, since gas/size accounting for the key blob happens only after or independent of the recursive decode cost.

### Impact Explanation
Deep unbounded recursion in Go can lead to significant stack growth per goroutine and, in the worst case, a runtime `fatal error: stack overflow`, which is unrecoverable via `recover()` and crashes the entire node process. Since transaction/RLP decoding of account keys happens during tx-pool admission (`AddRemote`/`AddLocal`) and during block execution/validation, a single malicious transaction or an `AccountKey` value reachable via public RPC (`kaia_decodeAccountKey`) could crash or hang honest nodes that attempt to decode it — a remotely triggerable Denial-of-Service, directly analogous to the Squid `X-Forwarded-For` uncontrolled-recursion DoS.

### Likelihood Explanation
Likelihood is High for reachability: the `Key` field of an `AccountUpdate`/`FeeDelegatedAccountUpdate`-family transaction, or the raw bytes passed to the `kaia_decodeAccountKey` RPC, are both directly attacker-controlled and require no special privilege, staking, or validator role — an ordinary externally-owned account or public RPC caller can submit the payload.

### Recommendation
Add an explicit recursion/nesting-depth guard in `AccountKeyRoleBased.DecodeRLP` (and/or in `AccountKeySerializer.DecodeRLP`) that rejects `AccountKeyTypeRoleBased` values nested inside another `AccountKeyRoleBased` *during decode*, rather than only after decoding completes — mirroring the existing `IsCompositeType`/`ErrNestedCompositeType` semantics but enforced at the RLP-decode boundary. Alternatively, track and cap decode nesting depth generically for `AccountKey` decoding.

### Proof of Concept
Conceptually: construct an `AccountKeySerializer` blob `B0` for keyType `AccountKeyTypeLegacy` (leaf). Then repeatedly wrap it: `Bi = RLP-encode(AccountKeySerializer{keyType: RoleBased, key: AccountKeyRoleBased{ AccountKeySerializer-encoding-of(B(i-1)) }})` for `i = 1..N` (e.g. `N = 50,000`). Submit a transaction (or call `kaia_decodeAccountKey`) whose `Key`/input bytes equal `B_N`. Decoding will recurse `N` times through `AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` before any nesting check is applied, exhausting the goroutine stack.

### Citations

**File:** blockchain/types/accountkey/account_key_role_based.go (L110-138)
```go
func (a *AccountKeyRoleBased) EncodeRLP(w io.Writer) error {
	enc := make([][]byte, len(*a))

	for i, k := range *a {
		enc[i], _ = rlp.EncodeToBytes(NewAccountKeySerializerWithAccountKey(k))
	}

	return rlp.Encode(w, enc)
}

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

**File:** blockchain/types/accountkey/account_key.go (L97-114)
```go
func NewAccountKey(t AccountKeyType) (AccountKey, error) {
	switch t {
	case AccountKeyTypeNil:
		return NewAccountKeyNil(), nil
	case AccountKeyTypeLegacy:
		return NewAccountKeyLegacy(), nil
	case AccountKeyTypePublic:
		return NewAccountKeyPublic(), nil
	case AccountKeyTypeFail:
		return NewAccountKeyFail(), nil
	case AccountKeyTypeWeightedMultiSig:
		return NewAccountKeyWeightedMultiSig(), nil
	case AccountKeyTypeRoleBased:
		return NewAccountKeyRoleBased(), nil
	}

	return nil, errUndefinedAccountKeyType
}
```

**File:** tests/account_keytype_test.go (L1697-1823)
```go
// TestAccountUpdateRoleBasedKeyNested tests account update with a nested RoleBasedKey.
// Nested RoleBasedKey is not allowed in Kaia.
// 1. Create an account with a RoleBasedKey.
// 2. Update an accountKey with a nested RoleBasedKey
func TestAccountUpdateRoleBasedKeyNested(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlTrace)
	prof := profile.NewProfiler()

	// Initialize blockchain
	start := time.Now()
	bcdata, err := NewBCData(6, 4)
	if err != nil {
		t.Fatal(err)
	}
	prof.Profile("main_init_blockchain", time.Now().Sub(start))
	defer bcdata.Shutdown()

	// Initialize address-balance map for verification
	start = time.Now()
	accountMap := NewAccountMap()
	if err := accountMap.Initialize(bcdata); err != nil {
		t.Fatal(err)
	}
	prof.Profile("main_init_accountMap", time.Now().Sub(start))

	// reservoir account
	reservoir := &TestAccountType{
		Addr:  *bcdata.addrs[0],
		Keys:  []*ecdsa.PrivateKey{bcdata.privKeys[0]},
		Nonce: uint64(0),
	}

	// anonymous account
	anon, err := createAnonymousAccount("98275a145bc1726eb0445433088f5f882f8a4a9499135239cfb4040e78991dab")
	assert.Equal(t, nil, err)

	// roleBasedKeys and a nested roleBasedKey
	roleKey, err := createDefaultAccount(accountkey.AccountKeyTypeRoleBased)
	assert.Equal(t, nil, err)

	nestedAccKey := accountkey.NewAccountKeyRoleBasedWithValues(accountkey.AccountKeyRoleBased{
		roleKey.AccKey,
	})

	if testing.Verbose() {
		fmt.Println("reservoirAddr = ", reservoir.Addr.String())
		fmt.Println("roleAddr = ", roleKey.Addr.String())
	}

	signer := types.LatestSignerForChainID(bcdata.bc.Config().ChainID)
	gasPrice := new(big.Int).SetUint64(bcdata.bc.Config().UnitPrice)

	// transfer (reservoir -> anon) using a legacy transaction.
	{
		var txs types.Transactions

		amount := new(big.Int).Mul(big.NewInt(3000), new(big.Int).SetUint64(params.KAIA))
		tx := types.NewTransaction(reservoir.Nonce,
			anon.Addr, amount, gasLimit, gasPrice, []byte{})

		err := tx.SignWithKeys(signer, reservoir.Keys)
		assert.Equal(t, nil, err)
		txs = append(txs, tx)

		if err := bcdata.GenABlockWithTransactions(accountMap, txs, prof); err != nil {
			t.Fatal(err)
		}
		reservoir.Nonce += 1
	}

	// update the account with a roleBased key.
	{
		var txs types.Transactions
		values := map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      anon.Nonce,
			types.TxValueKeyFrom:       anon.Addr,
			types.TxValueKeyGasLimit:   gasLimit,
			types.TxValueKeyGasPrice:   gasPrice,
			types.TxValueKeyAccountKey: roleKey.AccKey,
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeAccountUpdate, values)
		assert.Equal(t, nil, err)
		txs = append(txs, tx)

		err = tx.SignWithKeys(signer, anon.Keys)
		assert.Equal(t, nil, err)

		if err := bcdata.GenABlockWithTransactions(accountMap, txs, prof); err != nil {
			t.Fatal(err)
		}

		anon.Nonce += 1
	}

	// make TxPool to test validation in 'TxPool add' process
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

		// For block tx validation test
		{
			receipt, err := applyTransaction(t, bcdata, tx)
			assert.Equal(t, (*types.Receipt)(nil), receipt)
			assert.Equal(t, kerrors.ErrNestedCompositeType, err)
		}
	}
```
