### Title
Panic in `AssociateTx.AsEthereumData()` reachable from `EndBlock`'s deferred-info aggregation crashes/halts block processing - (File: x/evm/keeper/deferred.go)

### Summary
`GetAllEVMTxDeferredInfo`, called every block from `Keeper.EndBlock`, calls `msg.AsTransaction()` for any EVM message whose ante handler rejected it before deferred info was recorded. When that message wraps an `AssociateTx`, `AsTransaction()` internally calls `AsEthereumData()`, which is an unconditional `panic("not implemented")`. This mirrors the reported bug class: an invariant/assumption ("deferred info absent implies a regular tx we can safely re-derive an ethereum Transaction from") is checked/assumed in only some code paths, and a legitimate, attacker-reachable input (an ante-rejected `AssociateTx`) violates it, producing an unhandled panic in deterministic, consensus-critical block-processing code.

### Finding Description
`MsgEVMTransaction.AsTransaction()` unpacks the tx data and always calls `txData.AsEthereumData()`: [1](#0-0) 

For `AssociateTx`, `AsEthereumData()` is stubbed to always panic: [2](#0-1) 

`GetAllEVMTxDeferredInfo` iterates every EVM message recorded for the block. When no deferred-info entry exists for a tx index (which the code itself documents as happening "either in ante handler or due to a panic in msg server"), it unconditionally calls `msg.AsTransaction()` and only guards against a *nil* transaction, not a panic: [3](#0-2) 

The EVM ante pipeline handles `AssociateTx` as a distinct, short-circuiting path via `HandleAssociateTx`/`Preprocess`, and rejects it outright (before any deferred-info write) in ordinary, attacker-controllable conditions such as the account already being associated or having insufficient balance to force-associate: [4](#0-3) 

`k.msgs` (used by `GetAllEVMTxDeferredInfo`) is populated from every decoded EVM message in the block regardless of whether its ante handling succeeded, via `GetEVMMsg` in `app.ProcessBlock`, and `EndBlock` is invoked unconditionally afterward: [5](#0-4) 

So the sequence is: submit an `AssociateTx` that the ante handler will reject (e.g., re-associating an address that is already associated) → the message is still recorded in `k.msgs` for the block → in `EndBlock`, `GetAllEVMTxDeferredInfo` finds no deferred-info entry for that index → calls `msg.AsTransaction()` → hits `AssociateTx.AsEthereumData()` → unconditional `panic("not implemented")`. This panic is not caught anywhere between `EndBlock` and `GetAllEVMTxDeferredInfo`; only `ProcessBlock` further up the call stack has a `recover()`, but since block execution is deterministic consensus logic, every honest validator processing the same block hits the identical panic, turning a single-node crash into a deterministic, chain-wide halt.

### Impact Explanation
This is a validator/full-node halt triggerable by a single unprivileged EVM transaction (an `AssociateTx` engineered to be rejected by the ante handler after being counted into the block's message set). Because the panic is deterministic and identical for every node executing the same block, it can produce a full network liveness failure (chain halt) rather than a localized crash — qualifying as validator halt / block-delay-class impact called out in the acceptable-impact list.

### Likelihood Explanation
Triggering an ante-rejected `AssociateTx` requires no special privilege: any address that already completed association (an extremely common, ordinary state — e.g. any account that previously sent a normal EVM tx and thus got auto-associated) can be made to send another `AssociateTx`, which is rejected with "account already has association set" in the ante handler after the message has already been recorded for the block. No governance, validator collusion, or previously undisclosed state is required — the precondition ("already associated") is met by essentially any active account.

### Recommendation
- Make `AssociateTx.AsEthereumData()` (and other `AssociateTx` methods currently stubbed to panic) return a safe zero value or an explicit error instead of panicking, since `AssociateTx` is a legitimate, protocol-level message type that can flow through generic code paths like `AsTransaction()`.
- In `GetAllEVMTxDeferredInfo`, special-case `AssociateTx` (or any type without a real Ethereum representation) before calling `AsTransaction()`, mirroring the guards already present elsewhere (e.g., `msg.IsAssociateTx()` checks used in `evmrpc` and `msg_server.go`).
- Wrap `EndBlock`'s deferred-info aggregation in a recover/guard so a single malformed or edge-case message cannot crash the entire block-processing pipeline, and add fuzz/property tests feeding ante-rejected `AssociateTx` messages through `EndBlock`.

### Proof of Concept
1. Associate an EOA `A` normally (send any ordinary EVM tx, or an initial `AssociateTx`), so `A` has an entry in the address-association store.
2. Craft and submit a second `MsgEVMTransaction` wrapping an `AssociateTx` from the same EOA `A` (or an already-associated `seiAddr`).
3. During ante handling (`EVMPreprocessDecorator.AnteHandle`), the check `isAssociateTx && isAssociated` is true, so the ante handler returns the error `"account already has association set"` — the tx fails, but `app.ProcessBlock` still records this `MsgEVMTransaction` into `k.msgs` for the block index via `GetEVMMsg`.
4. At end of block, `Keeper.EndBlock` calls `GetAllEVMTxDeferredInfo`; for this tx index no deferred-info entry exists, so the code calls `etx, _ := msg.AsTransaction()`.
5. `AsTransaction()` unpacks the `AssociateTx` and calls `txData.AsEthereumData()`, which executes `panic("not implemented")`, crashing block finalization on every node executing this block. [1](#0-0) [2](#0-1) [6](#0-5) [7](#0-6)

### Citations

**File:** x/evm/types/message_evm_transaction.go (L67-74)
```go
func (msg *MsgEVMTransaction) AsTransaction() (*ethtypes.Transaction, ethtx.TxData) {
	txData, err := UnpackTxData(msg.Data)
	if err != nil {
		return nil, nil
	}

	return ethtypes.NewTx(txData.AsEthereumData()), txData
}
```

**File:** x/evm/types/ethtx/associate_tx.go (L39-39)
```go
func (tx *AssociateTx) AsEthereumData() ethtypes.TxData { panic("not implemented") }
```

**File:** x/evm/keeper/deferred.go (L13-32)
```go
func (k *Keeper) GetAllEVMTxDeferredInfo(ctx sdk.Context) (res []*types.DeferredInfo) {
	store := prefix.NewStore(ctx.TransientStore(k.transientStoreKey), types.DeferredInfoPrefix)
	for txIdx, msg := range k.msgs {
		txRes := k.txResults[txIdx]
		key := make([]byte, 8)
		binary.BigEndian.PutUint64(key, uint64(txIdx)) //nolint:gosec
		val := store.Get(key)
		if val == nil {
			if msg == nil {
				continue
			}
			// this means the transaction got reverted during execution, either in ante handler
			// or due to a panic in msg server
			etx, _ := msg.AsTransaction()
			if etx == nil {
				panic("etx is nil for EVM DeferredInfo msg.AsTransaction(). This should never happen.")
			}
			if txRes.Code == 0 {
				logger.Error("transaction has code 0 but no deferred info", "tx", etx.Hash())
			}
```

**File:** x/evm/ante/preprocess.go (L74-85)
```go
	isAssociateTx := derived.IsAssociate
	associateHelper := helpers.NewAssociationHelper(p.evmKeeper, p.evmKeeper.BankKeeper(), p.accountKeeper)
	_, isAssociated := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if isAssociateTx && isAssociated {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "account already has association set")
	} else if isAssociateTx {
		// check if the account has enough balance (without charging)
		if !p.IsAccountBalancePositive(ctx, seiAddr, evmAddr) {
			assocErr := evmtypes.NewAssociationMissingErr(seiAddr.String())
			evmAnteMetrics.associationError.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("scenario", "associate_tx_insufficient_funds"), attribute.String("type", assocErr.AddressType())))
			return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, "account needs to have at least 1 wei to force association")
		}
```

**File:** x/evm/keeper/abci.go (L106-106)
```go
	evmTxDeferredInfoList := k.GetAllEVMTxDeferredInfo(ctx)
```
