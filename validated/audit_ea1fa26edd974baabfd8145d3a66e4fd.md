### Title
Shared mutable `validatedSender`/`validatedFeePayer`/`validatedGas` fields on `*types.Transaction` allow cross-context/thread cache poisoning of fee-delegation resolution - ([File: blockchain/types/transaction.go])

### Summary
The libcurl CVE-2025-14017 bug class is: a per-call TLS setting (e.g. certificate verification) was mistakenly stored in shared/global state, so a value set for one thread's transfer leaked into and affected other threads' concurrent transfers. Kaia's `types.Transaction` has an analogous pattern: `AsMessageWithAccountKeyPicker` / `ValidateSender` / `ValidateFeePayer` compute per-call, per-context results (sender, fee payer, sig-validation gas) but cache them as mutable fields directly on the shared `*Transaction` object (`validatedSender`, `validatedFeePayer`, `validatedGas`), guarded only by a lock that protects the write itself, not the "is this the right context" invariant.

### Finding Description
`Transaction` caches validation results in shared fields: [1](#0-0) 

`ValidateSender` writes `validatedSender`/`validatedFeePayer` only if the current cached value is still the zero address ("first write wins"): [2](#0-1) [3](#0-2) 

`ValidateFeePayer` overwrites `validatedFeePayer` only if it still equals `validatedSender` (i.e., the first fee-payer resolution wins): [4](#0-3) 

`AsMessageWithAccountKeyPicker` computes `validatedGas` (intrinsic gas + sig-validation gas, which is a function of `currentBlockNumber` because `SigValidationGas`/`accountkey.ValidateAccountKey` are block-number-dependent, e.g. rule changes) and stores it directly on the shared pointer, then returns the *same* `*Transaction` pointer as the "message": [5](#0-4) 

The same `*types.Transaction` object (pooled in `TxPool`, referenced by `pool.all`/`pool.pending`) is handed concurrently to multiple independent execution contexts that each call `AsMessageWithAccountKeyPicker` with their own `signer`/`picker`/`currentBlockNumber`:
- speculative/pre-caching execution in `precacheTransaction` (`blockchain/state_prefetcher.go:92-104`), which can run in a background goroutine against a different `header.Number` than the block that ultimately gets applied,
- the real block-processing path in `blockchain/blockchain.go`,
- RPC tracing / state access paths in `node/cn/tracers/api.go` and `node/cn/state_accessor.go`, which can call the same tx object at an arbitrary historical/pending block number for `eth_call`/tracing.

Because the cached fields are "sticky" (first writer wins) and are stored on the transaction object itself rather than being scoped to the specific call/context, a result computed under one thread's context (e.g., prefetcher using `header.Number` X) becomes globally visible to every other concurrent or subsequent caller of the same tx object, even one intending to validate/execute at a different block number or with a different `AccountKeyPicker`/state view. This mirrors the curl bug precisely: a context-local configuration/result (TLS verify flag ↔ validated sender/fee payer/gas) is stored in shared object state instead of being threaded through the call, so it "possibly also affects other concurrently setup transfers."

The repo's own concurrency test acknowledges this exact race exists and asserts (only for the common case) that it resolves correctly: [6](#0-5) 

### Impact Explanation
If a fee-delegated transaction's `validatedFeePayer`/`validatedGas` are populated by a concurrent caller using a stale or different block context (e.g., the prefetcher racing ahead of, or lagging behind, the real applier, or an RPC trace call touching the same pooled tx concurrently with block assembly), the cached `SigValidateGas`/`IntrinsicGas` and fee-payer resolution used by the real `ApplyTransaction` path can silently be the ones computed under the *wrong* context. Since `AccountKeyPicker`-dependent role validation (`accountkey.ValidateAccountKey`) and `SigValidationGas` are block-number sensitive, this can result in: gas charged to the sender/fee-payer differing from what the correct-context computation would produce (fee/fee-delegation abuse), or account-key/role validation results from one snapshot leaking into execution against a different state snapshot, contributing to state divergence between honest nodes that race differently (e.g. one node's prefetcher wins the race, another's doesn't).

### Likelihood Explanation
The "first write wins" / "only overwrite if still equal to sender" guards make the benign single-context case deterministic, which is why normal operation and the existing race test pass. Triggering an observable divergence requires the same transaction object to be validated concurrently under genuinely different contexts (e.g., near a hard-fork boundary affecting `SigValidationGas`/`ValidateAccountKey`, or a state-prefetch that overlaps with a differing header), which is a narrower window than the general race — this substantially reduces (but the code does not eliminate) practical likelihood in the current codebase, and I could not find any test or code path that intentionally exercises differing-context concurrent validation to confirm an end-to-end exploit.

### Recommendation
Do not cache `validatedSender`/`validatedFeePayer`/`validatedGas` as mutable fields on the shared, pooled `*Transaction` object when it is exposed to multiple independent, potentially differing-context call sites (prefetch, tracing/RPC, real block processing). Instead, return a per-call, context-scoped message value (a copy or a wrapper struct carrying sender/fee payer/gas for that specific call) from `AsMessageWithAccountKeyPicker`, and only use the cached fields on the tx object as a "best-effort, same-context reuse" optimization guarded by also comparing the `currentBlockNumber`/context used to produce them, not merely "is it still zero" or "is it still equal to sender".

### Proof of Concept
Not independently reproduced end-to-end (would require constructing two contexts with differing `currentBlockNumber` around a hard-fork boundary that changes `SigValidationGas`/`ValidateAccountKey` behavior, and racing `precacheTransaction` against the real block-processing call on the same pooled `*Transaction`). The existing internal test demonstrates the underlying race exists on the exact same fields/methods: [7](#0-6)

### Citations

**File:** blockchain/types/transaction.go (L97-112)
```go
	// validatedSender represents the sender of the transaction to be used for ApplyTransaction().
	// This value is set in AsMessageWithAccountKeyPicker().
	validatedSender common.Address
	// validatedFeePayer represents the fee payer of the transaction to be used for ApplyTransaction().
	// This value is set in AsMessageWithAccountKeyPicker().
	validatedFeePayer common.Address
	// validatedGas holds intrinsic gas, sig validation gas, and number of tokens for the transaction to be used for ApplyTransaction().
	// This value is set in AsMessageWithAccountKeyPicker().
	validatedGas *ValidatedGas
	// The account's nonce is checked only if `checkNonce` is true.
	checkNonce bool
	// This value is set when the tx is invalidated in block tx validation, and is used to remove pending tx in txPool.
	markedUnexecutable atomic.Int32

	// lock for protecting fields in Transaction struct
	mu sync.RWMutex
```

**File:** blockchain/types/transaction.go (L709-740)
```go
func (tx *Transaction) AsMessageWithAccountKeyPicker(s Signer, picker AccountKeyPicker, currentBlockNumber uint64) (*Transaction, error) {
	intrinsicGas, err := tx.IntrinsicGas(currentBlockNumber)
	if err != nil {
		return nil, err
	}

	gasFrom, err := tx.ValidateSender(s, picker, currentBlockNumber)
	if err != nil {
		return nil, ErrSender(err)
	}

	tx.mu.Lock()
	tx.checkNonce = true
	tx.mu.Unlock()

	gasFeePayer := uint64(0)
	if tx.IsFeeDelegatedTransaction() {
		gasFeePayer, err = tx.ValidateFeePayer(s, picker, currentBlockNumber)
		if err != nil {
			return nil, ErrFeePayer(err)
		}
	}

	sigValidationGas := gasFrom + gasFeePayer
	intrinsicGas = intrinsicGas + sigValidationGas

	tx.mu.Lock()
	tx.validatedGas = &ValidatedGas{IntrinsicGas: intrinsicGas, SigValidateGas: sigValidationGas}
	tx.mu.Unlock()

	return tx, err
}
```

**File:** blockchain/types/transaction.go (L905-910)
```go
		tx.mu.Lock()
		if tx.validatedSender == (common.Address{}) {
			tx.validatedSender = addr
			tx.validatedFeePayer = addr
		}
		tx.mu.Unlock()
```

**File:** blockchain/types/transaction.go (L934-939)
```go
	tx.mu.Lock()
	if tx.validatedSender == (common.Address{}) {
		tx.validatedSender = from
		tx.validatedFeePayer = from
	}
	tx.mu.Unlock()
```

**File:** blockchain/types/transaction.go (L969-973)
```go
	tx.mu.Lock()
	if tx.validatedFeePayer == tx.validatedSender {
		tx.validatedFeePayer = feePayer
	}
	tx.mu.Unlock()
```

**File:** tests/race_test.go (L101-157)
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
	}

	for i := 0; i < 2*iterNum; i++ {
		if err := <-errCh; err != nil {
			t.Fatal(err)
		}
	}
}
```
