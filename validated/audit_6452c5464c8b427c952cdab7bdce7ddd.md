### Title
FinalizeBlock panic recovery converts a single malicious transaction's panic into a fatal ABCI error, causing a deterministic, permanently-replayed chain-wide crash loop - (File: `app/app.go`)

### Summary
`App.ProcessBlock` wraps the entire per-block transaction-execution pipeline in a `recover()` that turns *any* panic raised while executing a committed block's transactions into a returned Go `error` instead of allowing the block to succeed [1](#0-0) . When this happens during the real, consensus-decided path (`FinalizeBlocker`, not the speculative `ProcessProposal` optimistic-processing goroutine), that error is propagated verbatim out of `FinalizeBlocker` [2](#0-1)  and then out of `BaseApp.FinalizeBlock` as `return nil, err` [3](#0-2) . Per the documented ABCI contract, an error returned from `FinalizeBlock` is unrecoverable and "the Application must crash to ensure that the error is safely handled by an operator" [4](#0-3) . Because the panic-inducing transaction is already included in a finalized block, every validator that executes/replays that block hits the identical panic and crashes — including on restart, since Tendermint's crash-recovery/handshake mechanism re-applies blocks that were saved to the blockstore but not yet committed [5](#0-4) . This is structurally the same bug class as CVE-2025-32944: user-controlled input triggers an uncaught exception during processing of persisted data, and the crash recurs forever because the offending data cannot be removed from the persisted record that gets replayed on every restart — except here the "user-controlled input" is a transaction and the "persisted data" is a finalized/committed block.

### Finding Description
The panic-recovery pattern in `ProcessBlock` is explicitly designed with two very different outcomes depending on the caller:

- In `ProcessProposalHandler`'s optimistic-processing goroutine, a `ProcessBlock` panic/error is treated as *recoverable*: it just marks `optimisticProcessingInfo.Aborted = true` and the code comment states "ProcessBlock has panic recovery and returns error for any processing failures... not affecting proposal acceptance" [6](#0-5) .
- In `FinalizeBlocker`, which runs for the actually-decided block, the exact same `ProcessBlock` error is fatal: `if processErr != nil { ... return nil, processErr }` [2](#0-1) .

`BaseApp.FinalizeBlock` has no additional handling for this error — it simply forwards it: `if err != nil { return nil, err }` [3](#0-2) .

The ABCI/Tendermint specification is explicit that `FinalizeBlock` is one of the methods for which "there is no reasonable way to handle" an error, and the application must crash [4](#0-3) .

The codebase itself acknowledges that unvalidated `GetSigners()` calls can panic on malformed input reachable through a submitted transaction — many `sdk.Msg` implementations (`MsgSend`, `MsgGrant`, `MsgRevoke`, `MsgExec`, `MsgUnjail`, `MsgClearAdmin`, etc.) call `panic(err)` inside `GetSigners()` if the embedded address string fails bech32 decoding [7](#0-6) [8](#0-7) [9](#0-8) , and `MsgEVMTransaction.GetSigners()` unconditionally panics by design [10](#0-9) . The `ProcessProposalHandler` comment "All panics (including GetSigners) are handled in ProcessBlock, not affecting proposal acceptance" confirms the authors are aware that `GetSigners`-style panics are reachable inside the `ProcessBlock` pipeline for a submitted transaction [11](#0-10) . Any such panic that is not fully guarded before `FinalizeBlocker`'s call to `ProcessBlock` — for example within message routing/execution paths that are not gated a second time by `ValidateBasic` (nested authz message dispatch, OCC/giga concurrent execution paths, tx-priority/gas-estimation code invoked mid-block) — becomes a hard node crash for the *decided* block rather than a rejected proposal.

Because the transaction has already achieved 2/3+ commit and is stored in the block store, this is not a one-shot crash: every node (validator or full node) that executes or later replays that block (e.g., during crash recovery / ABCI handshake, which is documented to re-run `ApplyBlock` for blocks that were saved but not yet committed [5](#0-4) ) hits the identical deterministic panic and crashes again — precisely the "crash repeats infinitely on startup" behavior described in the PeerTube CVE, but affecting the entire validator set rather than a single server.

### Impact Explanation
If an unprivileged user can craft a single transaction that decodes and passes mempool/`CheckTx` validation but triggers a panic somewhere inside the `ProcessBlock` transaction-execution pipeline that is not converted into a normal `Code != 0` tx result before `ProcessBlock`'s outer `recover()` swallows it, that transaction — once included in a block by any proposer — will cause `FinalizeBlock` to return an error on every node. Given the ABCI contract, this forces every validator node to crash simultaneously, and because the bad transaction is permanently recorded in the finalized block, the crash recurs on every subsequent restart/replay attempt. This is a chain-wide, persistent halt (validator halt / block-production stoppage beyond the 2.5s threshold), which is a Medium/High severity denial-of-service impact matching the accepted impact categories.

### Likelihood Explanation
Likelihood depends on whether any exploitable path in msg routing/execution actually panics without first being screened by `ValidateBasic` inside the *committed*-block execution flow (as opposed to the many `recover()`-guarded helper functions such as `GetEVMMsg`, `DecodeTransactionsConcurrently`, and the tx prioritizer, all of which are already defensively wrapped). The explicit developer comment referencing unguarded "GetSigners panics" in `ProcessBlock`, combined with the fact that dozens of `Msg.GetSigners()` implementations across bank/authz/slashing/wasm/evm call `panic()` on malformed input, indicates the surface exists, but I was not able to fully trace a concrete unguarded call site within the time available (some candidates, like `AuthzNestedMessageDecorator`, only inspect nested messages via `GetMessages()`/type-switch and do not call `GetSigners()`, and the ante pipeline runs `ValidateBasic` before execution). The severity of the consequence (deterministic chain-wide crash-and-replay loop) is confirmed by code; the exact minimal transaction to trigger it is not fully confirmed.

### Recommendation
- Ensure `ProcessBlock`'s panic-`recover()` behaves identically regardless of caller: a panic during `FinalizeBlocker`'s execution of a *decided* block must never surface as a `FinalizeBlock`-level error. Instead, the panicking transaction should be converted into a non-zero-`Code` `ExecTxResult` (as is already done for ordinary execution failures) so the block still finalizes deterministically.
- Audit all message-execution and OCC/giga concurrent-execution code paths reachable from `ProcessBlock` for calls to `GetSigners()` (or other functions with `panic()` on malformed/attacker-controlled input) that occur without a preceding successful `ValidateBasic()` call.
- Add regression tests that submit deliberately malformed transactions (bad bech32 addresses inside nested `authz.MsgExec`, oversized/malformed `Any` payloads, etc.) through the full `FinalizeBlocker` path (not just `ProcessProposalHandler`) and assert the node does not crash and instead returns a coded tx failure.

### Proof of Concept
Conceptual PoC (exact minimal transaction not fully confirmed within the scope of this review):
1. Craft a transaction containing a message whose `GetSigners()` panics on malformed input (e.g., an `authz.MsgExec` wrapping an inner message with a corrupted address field, or any other msg type reachable through a path that calls `GetSigners()`/similar unguarded introspection during `ProcessBlock`'s concurrent decode/execute stages rather than through the standard ante `ValidateBasic` gate).
2. Get the transaction included by a proposer (it must pass `CheckTx`/mempool and `ProcessProposal`, which only marks optimistic processing as "aborted" on such a panic — it does not reject the block).
3. Once the block reaches consensus and `FinalizeBlocker` executes it, `ProcessBlock`'s `recover()` catches the panic and returns it as `err` [1](#0-0) , `FinalizeBlocker` propagates `nil, processErr` [2](#0-1) , and `BaseApp.FinalizeBlock` returns `nil, err` to the consensus engine [3](#0-2) .
4. Per the ABCI contract, this forces every node to crash on that height [4](#0-3) ; on restart, block-replay/crash-recovery re-executes the same finalized block and crashes again [5](#0-4) , producing an infinite, chain-wide crash loop until the binary is patched or the block is manually skipped by operators.

### Citations

**File:** app/app.go (L1229-1254)
```go
			go func() {
				// ProcessBlock has panic recovery and returns error for any processing failures
				// All panics (including GetSigners) are handled in ProcessBlock, not affecting proposal acceptance
				bpreq := &BlockProcessRequest{
					Hash:                req.Hash,
					ByzantineValidators: req.ByzantineValidators,
					Height:              req.Header.Height,
					Time:                req.Header.Time,
				}
				events, txResults, endBlockResp, processErr := app.ProcessBlock(ctx, req.Txs, bpreq, req.ProposedLastCommit, false, typedTxs)

				app.optimisticProcessingInfoMutex.Lock()
				if processErr != nil {
					// ProcessBlock failed (including GetSigners panics), mark as aborted
					logger.Info("ProcessBlock failed in optimistic processing", "err", processErr)
					app.optimisticProcessingInfo.Aborted = true
				} else {
					// ProcessBlock succeeded, store results
					app.optimisticProcessingInfo.Events = events
					app.optimisticProcessingInfo.TxRes = txResults
					app.optimisticProcessingInfo.EndBlockResp = endBlockResp
				}
				completion := app.optimisticProcessingInfo.Completion
				app.optimisticProcessingInfoMutex.Unlock()
				completion <- struct{}{}
			}()
```

**File:** app/app.go (L1335-1339)
```go
	events, txResults, endBlockResp, processErr := app.ProcessBlock(ctx, req.Txs, bpreq, req.DecidedLastCommit, false, nil)
	if processErr != nil {
		logger.Error("ProcessBlock failed in FinalizeBlocker", "err", processErr)
		return nil, processErr
	}
```

**File:** app/app.go (L1764-1781)
```go
func (app *App) ProcessBlock(ctx sdk.Context, txs [][]byte, req *BlockProcessRequest, lastCommit abci.CommitInfo, simulate bool, preDecoded []sdk.Tx) (events []abci.Event, txResults []*abci.ExecTxResult, endBlockResp abci.ResponseEndBlock, err error) {
	defer func() {
		if r := recover(); r != nil {
			panicMsg := fmt.Sprintf("%v", r)

			// Re-panic for upgrade-related panics to allow proper upgrade mechanism
			if upgradePanicRe.MatchString(panicMsg) {
				logger.Error("upgrade panic detected, panicking to trigger upgrade", "panic", r)
				panic(r) // Re-panic to trigger upgrade mechanism
			}
			stack := string(debug.Stack())
			logger.Error("panic recovered in ProcessBlock", "panic", r, "stack", stack)
			err = fmt.Errorf("ProcessBlock panic: %v", r)
			events = nil
			txResults = nil
			endBlockResp = abci.ResponseEndBlock{}
		}
	}()
```

**File:** sei-cosmos/baseapp/abci.go (L1079-1083)
```go
	if app.finalizeBlocker != nil {
		res, err := app.finalizeBlocker(app.deliverState.ctx, req)
		if err != nil {
			return nil, err
		}
```

**File:** sei-tendermint/spec/abci++/abci++_basic_concepts_002_draft.md (L271-273)
```markdown
has no reasonable way to handle. If there is an error in one
of these methods, the Application must crash to ensure that the error is safely
handled by an operator.
```

**File:** sei-tendermint/internal/state/store.go (L429-453)
```go
// given height from the database. If not found,
// ErrNoFinalizeBlockResponsesForHeight is returned.
//
// This is useful for recovering from crashes where we called app.Commit
// and before we called s.Save(). It can also be used to produce Merkle
// proofs of the result of txs.
func (store dbStore) LoadFinalizeBlockResponses(height int64) (*abci.ResponseFinalizeBlock, error) {
	buf, err := store.db.Get(finalizeBlockResponsesKey(height))
	if err != nil {
		return nil, err
	}
	if len(buf) == 0 {
		return nil, ErrNoFinalizeBlockResponsesForHeight{height}
	}

	finalizeBlockResponses := new(abci.ResponseFinalizeBlock)
	err = finalizeBlockResponses.Unmarshal(buf)
	if err != nil {
		// DATA HAS BEEN CORRUPTED OR THE SPEC HAS CHANGED
		panic(fmt.Sprintf("data has been corrupted or its spec has changed: %+v", err))
	}
	// TODO: ensure that buf is completely read.

	return finalizeBlockResponses, nil
}
```

**File:** sei-cosmos/x/bank/types/msgs.go (L57-64)
```go
// GetSigners Implements Msg.
func (msg MsgSend) GetSigners() []sdk.AccAddress {
	from, err := sdk.AccAddressFromBech32(msg.FromAddress)
	if err != nil {
		panic(err)
	}
	return []sdk.AccAddress{from}
}
```

**File:** sei-cosmos/x/authz/msgs.go (L208-215)
```go
// GetSigners implements Msg
func (msg MsgExec) GetSigners() []sdk.AccAddress {
	grantee, err := sdk.AccAddressFromBech32(msg.Grantee)
	if err != nil {
		panic(err)
	}
	return []sdk.AccAddress{grantee}
}
```

**File:** sei-wasmd/x/wasm/types/tx.go (L262-268)
```go
func (msg MsgClearAdmin) GetSigners() []sdk.AccAddress {
	senderAddr, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil { // should never happen as valid basic rejects invalid addresses
		panic(err.Error())
	}
	return []sdk.AccAddress{senderAddr}
}
```

**File:** x/evm/types/message_evm_transaction.go (L36-38)
```go
func (msg *MsgEVMTransaction) GetSigners() []sdk.AccAddress {
	panic("signer should be accessed on EVM transaction level")
}
```
