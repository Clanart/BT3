### Title
EVM fee is charged to a per-tx coinbase address before nonce/signature verification, and the funds are permanently stranded when that later check fails - ([File: app/ante.go], [File: x/evm/ante/fee.go], [File: x/evm/keeper/abci.go])

### Summary
The Sherlock report describes an accounting bug where a variable tracking assets pulled into `idle` gets clobbered by an unrelated calculation, silently losing previously-accounted funds. The Sei analog is structurally the same class of bug: value is credited to an ephemeral, per-transaction bookkeeping location (a synthetic "coinbase" address) and the subsequent aggregation step that is supposed to sweep/consume that value has an early-exit path that skips the sweep for a class of transactions — permanently orphaning the funds that were already moved there.

### Finding Description
The EVM ante-decorator chain is built in `app/ante.go`: [1](#0-0) 

`evmante.NewEVMFeeCheckDecorator` (fee validation / gas purchase) runs **before** `evmante.NewEVMSigVerifyDecorator` (signature/nonce verification), even though the module's own design doc states the intended order is signature/nonce verification first, then fee validation: [2](#0-1) 

Inside `EVMFeeCheckDecorator.AnteHandle`, the transaction's gas fee is purchased via `st.BuyGas()` and then **finalized/committed** to the real store via `stateDB.Finalize()`, with any excess tracked as ante-surplus: [3](#0-2) 

`BuyGas()` debits the sender and credits a deterministic, per-transaction "coinbase" address (`state.GetCoinbaseAddress(idx)`), which is only meant to be transient bookkeeping swept out at `EndBlock`: [4](#0-3) 

Because `EVMSigVerifyDecorator` (which performs the exact-nonce-match check for DeliverTx and marks `NonceBumped`) runs *after* the fee decorator already committed the fee payment, a transaction that fails nonce/signature verification has already had its gas fee moved into the per-tx coinbase address by the time it aborts.

At `EndBlock`, `x/evm/keeper/keeper.GetAllEVMTxDeferredInfo` synthesizes a `DeferredInfo{Error: txRes.Log}` for any tx that has no committed deferred-info entry (which is the case for ante failures, since `AppendToEvmTxDeferredInfo` is only called from the msg-server success path): [5](#0-4) 

The `EndBlock` aggregation loop then explicitly skips the coinbase-sweep and surplus-accumulation logic for any entry with a non-empty `Error` whose nonce was not bumped: [6](#0-5) 

```go
if deferredInfo.Error != "" && txHash.Cmp(ethtypes.EmptyTxsHash) != 0 {
    if !k.GetNonceBumped(ctx, deferredInfo.TxIndex) {
        continue   // <-- coinbase sweep + surplus.Add() below are never reached
    }
    ...
    continue
}
...
balance := useiBalance.Sub(lockedUseiBalance)
...
k.BankKeeper().SendCoinsAndWei(ctx, coinbaseAddress, coinbase, balance, weiBalance)
surplus = surplus.Add(deferredInfo.Surplus)
```

This is documented as intentional under the assumption that a nonce-mismatch failure "involves no state change": [7](#0-6) 

That assumption is false whenever the fee decorator (which does commit state via `Finalize()`) executes before the sig/nonce decorator that ultimately rejects the transaction, exactly as wired in `app/ante.go`. The gas payment sitting in `coinbaseAddress` for that tx index is never referenced by any other code path (each tx index's coinbase address is deterministic and only read/swept in this one `EndBlock` branch), so the funds are permanently unreachable — analogous to the original report's `idleIncrease` value being clobbered and never recovered.

### Impact Explanation
Whenever a transaction passes `EVMFeeCheckDecorator` (fee/gas purchase succeeds and is finalized) but is subsequently rejected by `EVMSigVerifyDecorator`'s nonce check, the user's paid gas fee is transferred out of their account and stranded at an ephemeral per-tx coinbase address that is never swept to the fee collector or the EVM module account, and never refunded to the sender. This is a permanent loss of user funds with no receipt, no event, and no mechanism to recover the balance, matching the "Accept" criteria for concrete fund loss / permanent freezing.

### Likelihood Explanation
This requires a normal, permissionless flow: any account submitting an EVM transaction where the effective fee/gas-purchase check succeeds but the final nonce comparison (exact match required for DeliverTx) fails afterward — e.g., a race where two transactions from the same sender land in the same block, or a resubmission with a slightly stale nonce after other txs from the sender have already bumped it during block execution. No privileged access or malicious behavior is needed. However, I was not able to fully trace go-ethereum's `BuyGas`/`core.StateTransition` internals in this index to confirm the exact mechanics of when balance is credited to the coinbase address versus refunded on early stateless-check failure, nor whether some other periodic sweep (outside this `EndBlock` branch) exists elsewhere in the codebase to reclaim these balances. This uncertainty should be verified with a live/dynamic trace before treating the severity as fully confirmed.

### Recommendation
Ensure fee purchase/finalization only occurs after all decorators that can reject the transaction without any compensating state changes (in particular nonce/signature verification) have succeeded, or alternatively make the `EndBlock` aggregation unconditionally sweep any coinbase balance for every tx index (whether or not `NonceBumped`/`Error` is set) before deciding what to do with the receipt, so that fee funds paid in `EVMFeeCheckDecorator` are never left unswept.

### Proof of Concept
Conceptual repro (not executed, given index-only investigation):
1. Submit an EVM `DynamicFeeTx` from account A with nonce N and sufficient balance/fee.
2. In the same block, submit another valid tx from account A that successfully consumes nonce N first (causing the sender's on-chain nonce to advance past N before the first tx's `EVMSigVerifyDecorator` step executes).
3. The first tx's `EVMFeeCheckDecorator` runs, `BuyGas()`+`Finalize()` commit the fee deduction from A into `state.GetCoinbaseAddress(idx)`.
4. `EVMSigVerifyDecorator` then detects the stale nonce and aborts the tx with an error; `SetNonceBumped` is never called for this tx index.
5. At `EndBlock`, `GetAllEVMTxDeferredInfo` synthesizes a `DeferredInfo{Error: "..."}` with `NonceBumped == false`, hitting the `continue` in `x/evm/keeper/abci.go` that skips both the coinbase sweep and surplus accumulation.
6. The fee A paid remains permanently in the unswept `coinbaseAddress` for that tx index, inaccessible to A, the fee collector, or the module account.

### Citations

**File:** app/ante.go (L91-100)
```go
	evmAnteDecorators := []sdk.AnteDecorator{
		// NOTE: NewEVMNoCosmosFieldsDecorator must come first to prevent writing state to chain without being charged.
		// E.g. EVMPreprocessDecorator may short-circuit all the later ante handlers if AssociateTx and ignore NewEVMNoCosmosFieldsDecorator.
		evmante.NewEVMNoCosmosFieldsDecorator(),
		evmante.NewEVMPreprocessDecorator(options.EVMKeeper, options.EVMKeeper.AccountKeeper()),
		evmante.NewBasicDecorator(options.EVMKeeper),
		evmante.NewEVMFeeCheckDecorator(options.EVMKeeper, options.UpgradeKeeper),
		evmante.NewEVMSigVerifyDecorator(options.EVMKeeper, options.LatestCtxGetter),
		evmante.NewGasDecorator(options.EVMKeeper),
	}
```

**File:** x/evm/AGENTS.md (L52-60)
```markdown
The EVM ante chain performs, in order:

1. **Cosmos field rejection** — EVM txs must not use Cosmos-specific fields (memo, timeout, fees, etc.).
2. **Preprocessing** — unpacks the inner Ethereum tx, recovers the sender via ECDSA, populates derived metadata. Associate transactions are handled here.
3. **Address derivation** — for Cosmos-signed txs, derives the EVM address from the public key and sets up mappings.
4. **Basic validation** — init code size, non-negative value, intrinsic gas.
5. **Signature/nonce verification** — validates chain ID and nonce. CheckTx uses pending nonce logic for mempool ordering; DeliverTx requires exact match.
6. **Fee validation** — checks against base fee and minimum fee, executes gas purchase, stores any fee surplus.
7. **Gas metering** — sets the Sei gas meter using the converted EVM gas limit.
```

**File:** x/evm/AGENTS.md (L64-75)
```markdown
## StateDB Bridge

The `state` package implements go-ethereum's `vm.StateDB` interface on top of Cosmos SDK stores. This is the core bridge that lets the EVM read and write state within the Cosmos framework.

Key design choices:

- **Balance representation** — Sei uses 6-decimal `usei` while EVM expects 18-decimal wei. The StateDB converts between them, tracking the sub-usei remainder (`wei`) separately.
- **Snapshots and reverts** — uses Cosmos `CacheMultiStore` for state snapshots. A journal records every state mutation so it can be rolled back on revert.
- **Deliver-tx code memo** — ordinary deliver `DBImpl` memos `GetCode` bytecode (`codeCache`). Simulation / RPC / trace / wasmd-entry leave it nil. Mutations go through `SetCode` or `RefreshCodeCache` (keeper bypasses) and are journaled per address so nested reverts do not wipe unrelated warms. `GetCodeSize` / `GetCodeHash` still read the store.
- **Transient state** — logs, transient storage (EIP-1153), access lists, and gas refunds are held in memory per-transaction and not persisted until finalization.
- **Coinbase collection** — each transaction gets a deterministic coinbase address for collecting fee surplus, which is swept to the fee collector at end-of-block.

```

**File:** x/evm/AGENTS.md (L157-161)
```markdown
### Receipts for Failure Scenarios

Unlike Ethereum, an EVM transaction on Sei could fail before it reaches the EVM (i.e. during ante handling).
- **Nonce Mismatch** - such failures would not result in any receipt, because they involve no state change
- **Others Ante Failures** - such failures would result in a status-0 receipt, because nonce for the sender will be incremented in such cases.
```

**File:** x/evm/ante/fee.go (L90-101)
```go
	if err := st.BuyGas(); err != nil {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, err.Error())
	}
	if !ctx.IsCheckTx() && !ctx.IsReCheckTx() {
		surplus, err := stateDB.Finalize()
		if err != nil {
			return ctx, err
		}
		if err := fc.evmKeeper.AddAnteSurplus(ctx, etx.Hash(), surplus); err != nil {
			return ctx, err
		}
	}
```

**File:** x/evm/keeper/deferred.go (L13-49)
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
			res = append(res, &types.DeferredInfo{
				TxIndex: uint32(txIdx), //nolint:gosec
				TxHash:  etx.Hash().Bytes(),
				Error:   txRes.Log,
			})
		} else {
			info := &types.DeferredInfo{}
			if err := info.Unmarshal(val); err != nil {
				// unable to unmarshal deferred info is serious, because it could cause
				// balance surplus to be mishandled and thus affect total supply
				panic(err)
			}
			res = append(res, info)
		}
	}
	return
}
```

**File:** x/evm/keeper/abci.go (L106-135)
```go
	evmTxDeferredInfoList := k.GetAllEVMTxDeferredInfo(ctx)
	denom := k.GetBaseDenom(ctx)
	surplus := k.GetAnteSurplusSum(ctx)
	for _, deferredInfo := range evmTxDeferredInfoList {
		txHash := common.BytesToHash(deferredInfo.TxHash)
		if deferredInfo.Error != "" && txHash.Cmp(ethtypes.EmptyTxsHash) != 0 {
			if !k.GetNonceBumped(ctx, deferredInfo.TxIndex) {
				continue
			}
			_ = k.SetTransientReceipt(ctx, txHash, &types.Receipt{
				TxHashHex:        txHash.Hex(),
				TransactionIndex: deferredInfo.TxIndex,
				VmError:          deferredInfo.Error,
				BlockNumber:      uint64(ctx.BlockHeight()), // nolint:gosec
			})
			continue
		}
		idx := int(deferredInfo.TxIndex)
		coinbaseAddress := state.GetCoinbaseAddress(idx)
		useiBalance := k.BankKeeper().GetBalance(ctx, coinbaseAddress, denom).Amount
		lockedUseiBalance := k.BankKeeper().LockedCoins(ctx, coinbaseAddress).AmountOf(denom)
		balance := useiBalance.Sub(lockedUseiBalance)
		weiBalance := k.BankKeeper().GetWeiBalance(ctx, coinbaseAddress)
		if !balance.IsZero() || !weiBalance.IsZero() {
			if err := k.BankKeeper().SendCoinsAndWei(ctx, coinbaseAddress, coinbase, balance, weiBalance); err != nil {
				logger.Error("failed to send usei surplus to coinbase account", "from", coinbaseAddress, "err", err)
			}
		}
		surplus = surplus.Add(deferredInfo.Surplus)
	}
```
