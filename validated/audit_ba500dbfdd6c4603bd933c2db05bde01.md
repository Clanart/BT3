### Title
Gasless module's "sender must be a plain EOA" check is validated only once at mempool admission using a stale state snapshot, allowing a sender to add code after admission and before block inclusion - (File: kaiax/gasless/impl/tx_pool.go, kaiax/gasless/impl/init.go)

### Summary
The gasless module's `ShouldCheckSenderCode` control is Kaia's analog of the `Address.isContract()` "EOA-only" assumption from the referenced report. It is used to reject `GaslessApproveTx`/`GaslessSwapTx` senders that are (or behave like) smart contracts, since the balance/allowance verification logic in the module assumes a plain EOA that cannot re-enter or otherwise manipulate token semantics during the proposer-funded lend/repay flow. The check is only executed once, at initial tx-pool admission, against a cached state snapshot, and is never re-verified when the transaction is promoted to pending, bundled by `ExtractTxBundles`, or actually executed. This creates a time-of-check/time-of-use gap in which an account can acquire code (e.g., via an account-update transaction) between admission and inclusion, defeating the "EOA-only" guarantee just as `Address.isContract()` can be defeated by code that appears later.

### Finding Description
`checkBalanceForApprove` and `checkBalanceForSwap` gate gasless transactions with: [1](#0-0) [2](#0-1) 

`getCurrentHasCode` answers this question using `g.currentState`, a cached `*state.StateDB` snapshot guarded by a mutex, not the state at the moment the transaction is actually bundled and executed: [3](#0-2) 

This function is invoked exclusively through `GetCheckBalance()`, which the tx pool calls when a transaction is first submitted/admitted: [4](#0-3) 

Crucially, none of the subsequent gating logic re-checks sender code:
- Promotion to pending only re-validates nonce ordering and `IsExecutable`: [5](#0-4) 
- `VerifyExecutable`, which is also exposed via the `debug_isGaslessTx` RPC, only checks sender-match, token-match, approve amount, nonce sequencing, and repay amount — no code check: [6](#0-5) 
- `ExtractTxBundles`, which assembles the final `[LendTxGenerator, ApproveTx, SwapTx]` bundle injected into the block, likewise performs no code check: [7](#0-6) 

Meanwhile, Kaia's account-update transaction types (Kaia's EIP-7702-like mechanism) can turn a previously code-less EOA into an account carrying delegated code, and this is enforced only at the point of that specific transaction's own validation (`validate7702`), independent of the gasless module's cached view of the sender: [8](#0-7) 

Because `g.currentState` is only refreshed on pool reset/new-block boundaries (not continuously per pending transaction), and the gasless-specific "no code" check is performed exactly once at admission time, a sender can:
1. Submit a `GaslessApproveTx`/`GaslessSwapTx` while the account is still a plain EOA — it passes `checkBalanceForApprove`/`checkBalanceForSwap` and is queued as a known/bundle tx.
2. Before the proposer builds the block containing that bundle, get an account-update ("set code") transaction for the same address included (a normal `TxTypeAccountUpdate`/related tx), which installs code and changes `acc.Type()`/`GetCodeHash` for that address.
3. When the proposer later extracts and executes the gasless bundle (`ExtractTxBundles` → block execution), the sender's account now has code, but nothing re-verifies `ShouldCheckSenderCode` at this stage — the original admission-time check is never repeated.

This mirrors the report's root cause precisely: a size/code-presence check (`extcodesize`-equivalent state read) is treated as a permanent EOA proof, when in fact code can legitimately appear on that address after the check was performed, and no code path re-validates the invariant closer to actual use.

### Impact Explanation
The "sender with code is not allowed" rule exists specifically to prevent non-EOA-controlled addresses from participating in the gasless lend/repay flow, where the block proposer (or CN) fronts KAIA gas on the assumption that repayment logic behaves like a standard token transfer from a plain account. If an account with delegated code (installed after admission) is allowed through despite the check's intent, custom code executing on `approveArgs.Sender`/`swapArgs.Sender`'s behalf during the ERC-20 approve/transferFrom/repay sequence could interact with allowance/balance state in ways the static pre-checks (`checkBalanceForApprove`/`checkBalanceForSwap`) did not account for at validation time, undermining the module's fee-delegation and repayment guarantees (proposer-funded lend not properly repaid, or unexpected reentrant behavior around token transfers during block building/execution). This directly touches "gasless and auction modules" fee-delegation/settlement integrity called out as in-scope.

### Likelihood Explanation
Exploitability requires only unprivileged, publicly reachable actions: (a) submitting a gasless approve/swap transaction pair, and (b) getting a normal account-update transaction for the same sender address included before the gasless bundle is picked up by the proposer — both are ordinary transactions any sender/public RPC caller can submit. No validator, peer, or operator privilege is needed; the race window is bounded by normal tx-pool/block-building timing (queue/pending timeouts of up to `QueueTimeout`/`PendingTimeout` = 10s each), which is plausible in a live network during proposer selection and block assembly.

### Recommendation
Do not treat the code-presence check as a one-time admission-time invariant. Re-verify `ShouldCheckSenderCode` (and ideally re-run the full `checkBalanceForApprove`/`checkBalanceForSwap` logic) immediately before bundling in `ExtractTxBundles` and/or as part of `VerifyExecutable`, using the state that will actually be used for execution, not the module's separately-cached `currentState` snapshot. Alternatively, fold the sender-code check directly into on-chain validation of the approve/swap transactions themselves (enforced at state-transition time) so it cannot be time-of-check/time-of-use bypassed by intervening account-update transactions.

### Proof of Concept
1. Attacker EOA `A` (no code) submits `GaslessApproveTx` + `GaslessSwapTx` for a legitimate token/router; `checkBalanceForApprove`/`checkBalanceForSwap` read `g.currentState.GetCodeHash(A) == EmptyCodeHash` and admit the pair into the pool (`kaiax/gasless/impl/tx_pool.go:74-126`, `kaiax/gasless/impl/init.go:132-137`).
2. Before the proposer packs a block containing this bundle, `A` submits (and gets included) an account-update transaction that installs delegated code at `A`, per Kaia's `TxTypeAccountUpdate` handling (`blockchain/types/tx_internal_data.go:782-799`).
3. The proposer later calls `ExtractTxBundles` on the still-queued gasless pair; no step in `ExtractTxBundles`, `IsExecutable`, or `VerifyExecutable` re-checks sender code (`kaiax/gasless/impl/builder.go:28-72`, `kaiax/gasless/impl/getter.go:203-266`), so the bundle `[LendTxGenerator, ApproveTx, SwapTx]` is built and executed with `A` now carrying delegated code — bypassing the intended "sender with code is not allowed" restriction that was only enforced against `A`'s prior, now-stale, code-less state.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L62-72)
```go
func (g *GaslessModule) GetCheckBalance() func(tx *types.Transaction) error {
	return func(tx *types.Transaction) error {
		if approveArgs, ok := decodeApproveTx(tx, g.signer); ok {
			return g.checkBalanceForApprove(approveArgs)
		}
		if swapArgs, ok := decodeSwapTx(tx, g.signer); ok {
			return g.checkBalanceForSwap(swapArgs, tx.Nonce())
		}
		return errors.New("not a gasless transaction") // should not happen because IsModuleTx is called before GetCheckBalance
	}
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L74-82)
```go
func (g *GaslessModule) checkBalanceForApprove(approveArgs *ApproveArgs) error {
	token := approveArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(approveArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L122-126)
```go
	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(swapArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L251-290)
```go
// isApproveTxReady assumes that the caller checked `g.IsApproveTx(approveTx)`
func (g *GaslessModule) isApproveTxReady(approveTx, nextTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, approveTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	if approveTx.Nonce() != nonce {
		return false
	}
	if nextTx == nil || !g.IsSwapTx(nextTx) {
		return false
	}

	return g.IsExecutable(approveTx, nextTx)
}

// isSwapTxReady assumes that the caller checked `g.IsSwapTx(swapTx)`
func (g *GaslessModule) isSwapTxReady(swapTx, prevTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, swapTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	var approveTx *types.Transaction
	if swapTx.Nonce() == nonce {
		approveTx = nil
	} else if swapTx.Nonce() == nonce+1 {
		if prevTx == nil || !g.IsApproveTx(prevTx) {
			return false
		}
		approveTx = prevTx
	} else {
		return false
	}

	return g.IsExecutable(approveTx, swapTx)
}
```

**File:** kaiax/gasless/impl/init.go (L111-137)
```go
func (g *GaslessModule) setCurrentState(state *state.StateDB) {
	g.currentStateMu.Lock()
	defer g.currentStateMu.Unlock()

	g.currentState = state
}

func (g *GaslessModule) getCurrentStateNonce(addr common.Address) uint64 {
	g.currentStateMu.Lock()
	defer g.currentStateMu.Unlock()

	return g.currentState.GetNonce(addr)
}

func (g *GaslessModule) getCurrentStateBalance(addr common.Address) *big.Int {
	g.currentStateMu.Lock()
	defer g.currentStateMu.Unlock()

	return g.currentState.GetBalance(addr)
}

func (g *GaslessModule) getCurrentHasCode(addr common.Address) bool {
	g.currentStateMu.Lock()
	defer g.currentStateMu.Unlock()

	return g.currentState.GetCodeHash(addr) != types.EmptyCodeHash
}
```

**File:** kaiax/gasless/impl/getter.go (L203-266)
```go
func (g *GaslessModule) IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool {
	err := g.VerifyExecutable(approveTxOrNil, swapTx)
	if err != nil {
		return false
	}
	return true
}

// VerifyExecutable checks if the given transactions form a valid gasless transaction
// It returns an error explaining why the transaction is not executable if it's not,
// and a boolean indicating whether the transaction is executable
func (g *GaslessModule) VerifyExecutable(approveTxOrNil, swapTx *types.Transaction) error {
	// Sx.
	swapArgs, ok := decodeSwapTx(swapTx, g.signer)
	if !ok {
		return ErrDecodeSwapTx
	}
	if !g.isSwapTx(swapArgs) {
		return ErrSwapTxInvalid
	}

	// Conditions involving ApproveTx
	if approveTxOrNil != nil {
		// Ax.
		approveArgs, ok := decodeApproveTx(approveTxOrNil, g.signer)
		if !ok {
			return ErrDecodeApproveTx
		}
		if !g.isApproveTx(approveArgs) {
			return ErrApproveTxInvalid
		}
		// AP1.
		if approveArgs.Sender != swapArgs.Sender {
			return ErrDifferentSenders
		}
		// SP1.
		if approveArgs.Token != swapArgs.Token {
			return fmt.Errorf("%w: approve token %s, swap token %s", ErrDifferentTokens, approveArgs.Token.Hex(), swapArgs.Token.Hex())
		}
		// SP2.
		if approveArgs.Amount.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("%w: approve amount %s, required amount %s", ErrInsufficientApproveAmount, approveArgs.Amount.String(), swapArgs.AmountIn.String())
		}
		// SP3.
		if approveTxOrNil.Nonce()+1 != swapTx.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, swap nonce %d (expected %d)", ErrNonSequentialNonce, approveTxOrNil.Nonce(), swapTx.Nonce(), approveTxOrNil.Nonce()+1)
		}
		if nonce := g.getCurrentStateNonce(approveArgs.Sender); nonce != approveTxOrNil.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, current nonce %d", ErrApproveNonceNotCurrent, approveTxOrNil.Nonce(), nonce)
		}
	} else {
		// SP3.
		if nonce := g.getCurrentStateNonce(swapArgs.Sender); nonce != swapTx.Nonce() {
			return fmt.Errorf("%w: swap nonce %d, current nonce %d", ErrSwapNonceNotCurrent, swapTx.Nonce(), nonce)
		}
	}

	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
	}

	return nil
}
```

**File:** kaiax/gasless/impl/builder.go (L28-72)
```go
func (g *GaslessModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	// there are only at most two gasless transactions in pending for a sender
	bundles := []*builder.Bundle{}
	approveTxs := map[common.Address]*types.Transaction{}
	targetTxHash := common.Hash{}
	for _, tx := range txs {
		addr, err := types.Sender(g.signer, tx)
		if err != nil {
			continue
		}
		if g.IsApproveTx(tx) {
			approveTxs[addr] = tx
		} else if g.IsSwapTx(tx) && g.IsExecutable(approveTxs[addr], tx) {
			bundleTxs := builder.NewTxOrGenList(g.GetLendTxGenerator(approveTxs[addr], tx))
			if approveTxs[addr] != nil {
				bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(approveTxs[addr]))
			}
			bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(tx))

			b := builder.NewBundle(
				bundleTxs,
				targetTxHash,
				false,
			)

			targetTxHash = tx.Hash()

			isConflict := false
			for _, prev := range append(prevBundles, bundles...) {
				if prev.IsConflict(b) {
					isConflict = true
					break
				}
			}
			if isConflict {
				// Gasless transactions will just fail even if they aren't bundled.
				continue
			}
			bundles = append(bundles, b)
		} else {
			targetTxHash = tx.Hash()
		}
	}
	return bundles
}
```

**File:** blockchain/types/tx_internal_data.go (L759-799)
```go
func validate7702(stateDB StateDB, txType TxType, from, to common.Address) error {
	switch txType {
	// Group 1: Recipient must be EOA without code
	case TxTypeValueTransfer,
		TxTypeFeeDelegatedValueTransfer,
		TxTypeFeeDelegatedValueTransferWithRatio,
		TxTypeValueTransferMemo,
		TxTypeFeeDelegatedValueTransferMemo,
		TxTypeFeeDelegatedValueTransferMemoWithRatio:
		acc := stateDB.GetAccount(to)
		if acc == nil {
			return nil
		}
		if acc.Type() == account.SmartContractAccountType {
			return kerrors.ErrToMustBeEOAWithoutCode
		}
		eoa, ok := acc.(*account.ExternallyOwnedAccount)
		if !ok || !bytes.Equal(eoa.GetCodeHash(), emptyCodeHash) {
			return kerrors.ErrToMustBeEOAWithoutCode
		}

		return nil

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
