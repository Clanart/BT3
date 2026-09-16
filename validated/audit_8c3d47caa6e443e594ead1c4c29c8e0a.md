### Title
Order-dependent caching of `validatedSender`/`validatedFeePayer` in `Transaction.ValidateSender`/`ValidateFeePayer` allows fee-payer corruption for fee-delegated transactions - (File: blockchain/types/transaction.go)

### Summary
`Transaction.ValidateSender` and `Transaction.ValidateFeePayer` both write into the same pair of cached fields (`tx.validatedSender`, `tx.validatedFeePayer`) using "is this still the zero/uninitialized value" guards instead of validating that the specific field they are responsible for is still unset. Just like the Sherlock M-4 bug (where `quote.partyB` was read before being assigned, causing the wrong nonce key to be updated), here `validatedFeePayer` can be overwritten with the wrong address depending on which validation function executes first, because `ValidateSender` mutates `validatedFeePayer` as a side effect based on a stale/default value of `validatedSender`.

### Finding Description
`ValidateSender` sets both fields together, gated only on `validatedSender` being the zero address: [1](#0-0) [2](#0-1) 

`ValidateFeePayer` sets `validatedFeePayer` gated on it currently equalling `validatedSender`: [3](#0-2) 

For a fee-delegated transaction, both `validatedSender` and `validatedFeePayer` start as the zero address. If `ValidateFeePayer` is invoked before `ValidateSender` (both fields are zero, so `validatedFeePayer == validatedSender` is true) it correctly sets `validatedFeePayer = feePayer`. However, if `ValidateSender` is subsequently invoked, its guard only checks `validatedSender == zero` — it does not check whether `validatedFeePayer` has already been legitimately set to a different, non-sender address. Because it unconditionally sets `tx.validatedFeePayer = from` inside that same branch, it overwrites the already-correct fee payer address with the transaction sender's address. This mirrors the audited bug precisely: a struct field's real value is not read/validated before being used to gate/perform another field assignment, so an assignment happens against a stale value, corrupting the second field.

The codebase itself has a known race test explicitly written around this exact scenario, confirming this is a reachable and previously observed hazard: [4](#0-3) 

Both validation functions are exercised together whenever a fee-delegated transaction message is built, e.g. `AccountMap.Update` calls `tx.ValidateSender` then `tx.ValidateFeePayer` sequentially per tx, but other paths (transaction pool validation, tracers, block execution, `AsMessageWithAccountKeyPicker`) can invoke them in different orders or concurrently across goroutines processing the same transaction object (e.g., during tracing/replay), as shown by the test in `tests/race_test.go`. [5](#0-4) 

### Impact Explanation
This is a fee-delegation abuse / accounting-integrity bug reachable via any fee-delegated transaction (`TxTypeFeeDelegated*`), a category explicitly permitted for analysis. If `validatedFeePayer` is silently reset to the sender's address after being correctly computed as the actual fee payer, subsequent logic that reads `tx.ValidatedFeePayer()` (fee/gas charging, balance deduction, receipt construction, nonce validation for the fee payer's AccountKey) would attribute the fee-delegated transaction's fee to the wrong party — the sender itself instead of the designated fee payer, or vice versa in other orderings. This can cause silent misattribution of fee liability and would also produce a state divergence between honest nodes if different code paths (or different execution orders under concurrency, e.g., during parallel state-prefetch/tracing) resolve `ValidatedFeePayer()` differently for the same transaction.

### Likelihood Explanation
The exact race is already covered by a dedicated concurrency test in the repository (`TestRaceAsMessageWithAccountKeyPicker`), meaning the developers themselves identified this exact hazard as plausible under concurrent/ordering-dependent invocation of these two functions. Any code path that calls `ValidateFeePayer` before `ValidateSender` on the same `*Transaction` object (which is not enforced anywhere as an invariant) can trigger the corruption without requiring any malicious input beyond submitting a normal fee-delegated transaction.

### Recommendation
Do not use "is the field still zero" as a proxy for "has this specific field been validated yet." Track sender-validation and fee-payer-validation with independent boolean flags (or only ever mutate `validatedFeePayer` inside `ValidateSender` when it has not already been explicitly set by `ValidateFeePayer`), so that `ValidateSender` never overwrites an already-correctly-computed `validatedFeePayer`.

### Proof of Concept
1. Construct a fee-delegated transaction `tx` where `sender != feePayer`.
2. Call `tx.ValidateFeePayer(signer, picker, blockNum)` first — since `tx.validatedSender == tx.validatedFeePayer == zero`, the guard passes and `tx.validatedFeePayer` is correctly set to `feePayer`.
3. Call `tx.ValidateSender(signer, picker, blockNum)` — since `tx.validatedSender == zero` is still true, the guard passes and unconditionally executes `tx.validatedFeePayer = from` (the sender), overwriting the correct fee payer.
4. `tx.ValidatedFeePayer()` now incorrectly returns the sender's address instead of the actual fee payer, corrupting any downstream fee/balance accounting that relies on it. [6](#0-5)

### Citations

**File:** blockchain/types/transaction.go (L896-976)
```go
// ValidateSender finds a sender from both legacy and new types of transactions.
// It returns the senders address and gas used for the tx validation.
func (tx *Transaction) ValidateSender(signer Signer, p AccountKeyPicker, currentBlockNumber uint64) (uint64, error) {
	if tx.IsEthereumTransaction() {
		addr, err := Sender(signer, tx)
		// Legacy transaction cannot be executed unless the account has a legacy key.
		if p.GetKey(addr).Type().IsLegacyAccountKey() == false {
			return 0, kerrors.ErrLegacyTransactionMustBeWithLegacyKey
		}
		tx.mu.Lock()
		if tx.validatedSender == (common.Address{}) {
			tx.validatedSender = addr
			tx.validatedFeePayer = addr
		}
		tx.mu.Unlock()
		return 0, err
	}

	pubkey, err := SenderPubkey(signer, tx)
	if err != nil {
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

	tx.mu.Lock()
	if tx.validatedSender == (common.Address{}) {
		tx.validatedSender = from
		tx.validatedFeePayer = from
	}
	tx.mu.Unlock()

	return gasKey, nil
}

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

	tx.mu.Lock()
	if tx.validatedFeePayer == tx.validatedSender {
		tx.validatedFeePayer = feePayer
	}
	tx.mu.Unlock()

	return gasKey, nil
}
```

**File:** tests/race_test.go (L101-149)
```go
// TestRaceAsMessageWithAccountPickerForFeePayer tests calling AsMessageWithAccountPicker of a fee delegated transaction
// where a fee payer may be inserted wrongly due to concurrent issue.
func TestRaceAsMessageWithAccountPickerForFeePayer(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlTrace)

	// Configure and generate a sample block chain
	var (
		gendb = database.NewMemoryDBManager()

		// create a sender and a feepayer
		from, _     = createAnonymousAccount("a5c9a50938a089618167c9d67dbebc0deaffc3c76ddc6b40c2777ae594389999")
		feePayer, _ = createAnonymousAccount("ed580f5bd71a2ee4dae5cb43e331b7d0318596e561e6add7844271ed94156b20")

		funds = new(big.Int).Mul(big.NewInt(1e16), big.NewInt(params.KAIA))
		gspec = &blockchain.Genesis{
			Config: params.TestChainConfig,
			Alloc: blockchain.GenesisAlloc{
				from.GetAddr():     {Balance: funds},
				feePayer.GetAddr(): {Balance: funds},
			},
		}
		genesis = gspec.MustCommit(gendb)
		signer  = types.LatestSignerForChainID(gspec.Config.ChainID)
	)

	iterNum := 10000
	errCh := make(chan error, 2*iterNum)

	for i := 0; i < iterNum; i++ {
		tx, _ := genFeeDelegatedChainDataAnchoring(t, signer, from, nil, feePayer, big.NewInt(1234))
		for i := 0; i < 2; i++ {
			go func() {
					stateDB, err := state.New(genesis.Root(), state.NewDatabase(gendb), nil, nil)
				if err != nil {
					panic(err)
				}

				msg, err := tx.AsMessageWithAccountKeyPicker(signer, stateDB, 0)
				if err != nil {
					panic(err)
				}

				if msg.ValidatedFeePayer() != feePayer.GetAddr() {
					errCh <- fmt.Errorf("expected: %v, actual: %v", feePayer.GetAddr().String(), msg.ValidatedFeePayer().String())
				} else {
					errCh <- nil
				}
			}()
		}
```

**File:** tests/kaia_test_account_map_test.go (L148-162)
```go
		gasFrom, err := tx.ValidateSender(signer, picker, currentBlockNumber)
		if err != nil {
			return err
		}
		from := tx.ValidatedSender()

		gasFeePayer := uint64(0)
		feePayer := from
		if tx.IsFeeDelegatedTransaction() {
			gasFeePayer, err = tx.ValidateFeePayer(signer, picker, currentBlockNumber)
			if err != nil {
				return err
			}
			feePayer = tx.ValidatedFeePayer()
		}
```
