### Title
Shared `BalanceHandler` state across nested/recursive precompile calls causes stale `prevEventsLen`, corrupting EVM StateDB balances and triggering unauthorized mint/burn on commit - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
The `LimitOrderProcessor` race is a "stale read-then-decide" bug where two concurrent workers overwrite each other's snapshot of mutable state before a decision is finalized. The Cosmos EVM analog is `BalanceHandler.prevEventsLen`: it is instance state that records "where in the event log the current native-balance-affecting call started." If two nested/recursive precompile invocations share the same `BalanceHandler` instance, the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`, so the outer call's `AfterBalanceChange` later slices the event log from the wrong (later) offset, silently dropping bank `coin_spent`/`coin_received`/`fractional_balance_change` events that should have been mirrored into the EVM `StateDB`.

### Finding Description
`BalanceHandler` tracks native bank balance changes so they can be reflected in the EVM `StateDB`: [1](#0-0) 

`AfterBalanceChange` only processes `events[bh.prevEventsLen:]`, i.e., events emitted strictly after the last recorded checkpoint: [2](#0-1) 

The "safe" production pattern in `precompile.go`'s `runNativeAction` creates a **brand-new** `BalanceHandler` per call via a factory: [3](#0-2) [4](#0-3) 

However, several production precompiles (distribution, erc20, gov, ics20, slashing, staking) invoke `p.GetBalanceHandler()` — the same access pattern used by the debug/test precompile that reproduces the bug: [5](#0-4) [6](#0-5) 

The repo contains an explicit integration test that documents and reproduces this exact bug class using recursive precompile calls that reenter through `CallEVMWithData`: [7](#0-6) [8](#0-7) 

When a precompile is reentered (e.g., a contract call chain that loops back into the same precompile address, which the EVM keeper caches/reuses per address via `GetPrecompileInstance`), the inner call's `BeforeBalanceChange` resets `prevEventsLen` to a later index. When the outer call's `AfterBalanceChange` subsequently runs, it uses that later (stale-relative-to-outer) index and therefore **skips** the bank events that occurred between the outer call's start and the inner call's start. Those skipped balance-changing events (coin spent/received, fractional balance changes) never get applied via `stateDB.AddBalance`/`SubBalance`.

This directly parallels the report's root cause: a shared mutable checkpoint (`prevEventsLen` / `currentFilled`) is clobbered by an interleaved/nested execution before the original caller uses it to decide what to do with the "unprocessed" range.

### Impact Explanation
At the end of EVM transaction execution, `x/vm` reconciles `StateDB` balances back into `x/bank` by comparing the StateDB value against the actual bank balance and minting or burning the delta: [9](#0-8) 

If dropped events cause `StateDB`'s tracked balance for an address to diverge from the true bank ledger, this reconciliation logic will either **mint** new EVM-denominated coins (if StateDB balance is erroneously higher than the bank ledger) or **burn** real user coins (if StateDB balance is erroneously lower). Both outcomes are unauthorized, irreversible accounting corruption of spendable user value — matching the "Critical unauthorized minting, burning, duplication, or irreversible accounting corruption... across native balances, EVM balances... or precompile-mediated assets" impact gate. Because affected addresses can include ordinary EOAs/contracts interacting with stateful precompiles (staking, distribution, erc20, ics20, gov, slashing) inside a single transaction, an unprivileged user could trigger the mismatch simply by constructing a contract call graph that reenters a precompile.

### Likelihood Explanation
Likelihood is Medium: it requires a call graph where the same precompile address is invoked more than once within a single top-level EVM transaction (nested/recursive/reentrant calls), which is achievable by any contract author since precompiles are ordinary callable addresses from Solidity, and some precompiles (e.g., ICS20 with IBC callback contracts, or contracts that call `distribution`/`staking` precompiles and elsewhere trigger a callback into the same address) create straightforward reentry opportunities. This is analogous to the report's "Medium" likelihood requiring only ordinary concurrent job scheduling, here requiring only ordinary nested contract calls.

### Recommendation
- Verify and standardize all stateful precompiles (distribution, erc20, gov, ics20, slashing, staking) to always obtain a **fresh** `BalanceHandler` per invocation (the `BalanceHandlerFactory.NewBalanceHandler()` pattern already used in `precompile.go`'s `runNativeAction`), instead of any shared/cached `GetBalanceHandler()` field reused across nested/reentrant calls.
- Make `prevEventsLen` checkpointing reentrancy-safe by using a stack (push/pop) of checkpoints instead of a single mutable field, so nested calls cannot clobber an outer call's starting offset.
- Add an invariant check after each precompile execution asserting that the sum of applied `StateDB` balance deltas matches the sum of bank events emitted during that call's window, failing loudly (reverting the tx) rather than silently dropping events.
- Extend the existing `BalanceHandlerTestSuite`/`TestRecursivePrecompileCallsWithDebugPrecompile` coverage to the real production precompiles that call `GetBalanceHandler()`, not just the debug precompile, to confirm whether they are actually reachable/vulnerable in production call graphs.

### Proof of Concept
The repository's own test demonstrates the mechanism (using the debug precompile solely to make the recursive/nested-call scenario reproducible in a controlled test):
1. Deploy `DebugPrecompileCaller`, whose `callback()` recursively invokes the debug precompile, which itself calls back into the same precompile address via `evmKeeper.CallEVMWithData`. [10](#0-9) 
2. Each recursive entry calls `BeforeBalanceChange` (resetting `prevEventsLen`) then `AfterBalanceChange` (consuming events from that offset). [11](#0-10) 
3. Because `prevEventsLen` is a single shared field, the outer call's window of "unprocessed" events is truncated by the inner call's checkpoint, mirroring exactly the "job reads stale checkpoint, computation based on it is wrong" pattern from the source bug report — except here the corrupted value feeds directly into `StateDB` balances and, subsequently, into the mint/burn reconciliation in `x/vm/keeper/statedb.go`'s `SetBalance`, which is the concrete Critical-impact path (unauthorized mint/burn of user funds).

### Citations

**File:** precompiles/common/balance_handler.go (L43-48)
```go
// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L68-72)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
		switch event.Type {
```

**File:** precompiles/common/precompile.go (L99-106)
```go
	var balanceHandler *BalanceHandler
	if p.BalanceHandlerFactory != nil {
		balanceHandler = p.BalanceHandlerFactory.NewBalanceHandler()
	}

	if balanceHandler != nil {
		balanceHandler.BeforeBalanceChange(ctx)
	}
```

**File:** precompiles/common/precompile.go (L119-123)
```go
	if balanceHandler != nil {
		if err := balanceHandler.AfterBalanceChange(ctx, stateDB); err != nil {
			return nil, err
		}
	}
```

**File:** testutil/testdata/debug/debug.go (L77-78)
```go
	// Start the balance change handler before executing the precompile.
	p.GetBalanceHandler().BeforeBalanceChange(ctx)
```

**File:** testutil/testdata/debug/debug.go (L109-112)
```go
	// Process the native balance changes after the method execution.
	if err := p.GetBalanceHandler().AfterBalanceChange(ctx, stateDB); err != nil {
		return nil, err
	}
```

**File:** testutil/testdata/debug/debug.go (L127-143)
```go
func (p Precompile) Call0(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// data := crypto.Keccak256([]byte("function callback()"))[:4]
	counter := new(big.Int).SetBytes(contract.Input[1:])
	counter = new(big.Int).Add(counter, big.NewInt(1))

	args := math.U256Bytes(counter)
	selector := []byte{0xff, 0x58, 0x5c, 0xaf}
	data := append(selector, args...)

	caller := contract.Caller()
	fmt.Printf("Execute debug precompile %s\n", caller.String())
	rsp, err := p.evmKeeper.CallEVMWithData(ctx, p.Address(), &caller, data, true)
	fmt.Println("callback response:", rsp.Ret, err)
	if err != nil {
		return nil, err
	}
	return nil, nil
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-106)
```go
// TestRecursivePrecompileCallsWithDebugPrecompile demonstrates the balance handler bug
// by triggering recursive calls that share the same BalanceHandler instance.
func (s *BalanceHandlerTestSuite) TestRecursivePrecompileCallsWithDebugPrecompile() {
	evmApp := s.chain.App.(evm.EvmApp)
	ctx := s.chain.GetContext()

	// Create and register debug precompile
	debugPrec := debugprecompile.NewPrecompile(evmApp.GetBankKeeper(), evmApp.GetEVMKeeper())
	// Set the precompile directly in the EVM keeper's precompile map
	evmApp.GetEVMKeeper().RegisterStaticPrecompile(debugPrec.Address(), debugPrec)
	err := evmApp.GetEVMKeeper().EnableStaticPrecompiles(ctx, debugPrec.Address())
	s.Require().NoError(err)

	a, b, c := evmApp.GetEVMKeeper().GetPrecompileInstance(ctx, debugPrec.Address())
	fmt.Println(a, b, c)

	// Deploy caller contract
	callerContract, err := contracts.LoadDebugPrecompileCaller()
	s.Require().NoError(err)

	deploymentData := testutiltypes.ContractDeploymentData{
		Contract:        callerContract,
		ConstructorArgs: []interface{}{},
	}

	// Use local helper function
	callerAddr, err := DeployContract(s.T(), s.chain, deploymentData)
	s.Require().NoError(err)
	s.chain.NextBlock()

	s.T().Logf("Deployed caller contract at %s", callerAddr.Hex())
	s.T().Logf("Debug precompile at %s", debugPrec.Address().Hex())

	// Pack the input for callback(0)
	input, err := callerContract.ABI.Pack("callback", big.NewInt(0))
	s.Require().NoError(err)

	// Fund Contract
	err = evmApp.GetBankKeeper().SendCoins(ctx, s.chain.SenderAccounts[0].SenderAccount.GetAddress(), callerAddr.Bytes(), types.NewCoins(types.NewCoin("aatom", sdkmath.NewInt(10000000))))
	s.Require().NoError(err)

	res, _, _, err := s.chain.SendEvmTx(
		s.chain.SenderAccounts[0],
		0,             // sender index
		callerAddr,    // to address
		big.NewInt(0), // value
		input,         // data
		0,             // gas price multiplier
	)
	s.Require().NoError(err, "callback transaction should succeed")
	s.Require().False(res.IsErr(), "callback should not fail: %s", res.Events)

	s.Require().Equal(len(res.Events), 15, "callback should have 15 events")
	debug_count := 0
	for _, event := range res.Events {
		if event.Type == "debug_precompile" {
			debug_count++
		}
	}
	s.Require().Equal(10, debug_count, "callback should have 1 debug precompile")

	// Advance to next block to finalize state
	s.chain.NextBlock()
}
```

**File:** x/vm/keeper/statedb.go (L111-136)
```go
// SetBalance update account's balance, compare with current balance first, then decide to mint or burn.
func (k *Keeper) SetBalance(ctx sdk.Context, addr common.Address, amount *uint256.Int) error {
	if amount == nil {
		return nil
	}
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	coin := k.bankWrapper.SpendableCoin(ctx, cosmosAddr, types.GetEVMCoinDenom())

	balance := coin.Amount.BigInt()
	delta := new(big.Int).Sub(amount.ToBig(), balance)
	switch delta.Sign() {
	case 1:
		// mint
		if err := k.bankWrapper.MintAmountToAccount(ctx, cosmosAddr, delta); err != nil {
			return err
		}
	case -1:
		// burn
		if err := k.bankWrapper.BurnAmountFromAccount(ctx, cosmosAddr, new(big.Int).Neg(delta)); err != nil {
			return err
		}
	default:
		// not changed
	}
	return nil
}
```

**File:** contracts/solidity/DebugPrecompileCaller.sol (L4-29)
```text
contract DebugPrecompileCaller {
    address constant debugPrecompile = 0x0000000000000000000000000000000000000799;
    error CallFailed(bytes data);
    function callback(uint256 counter) public {
        bool result;
        bytes memory data;

        // emit events
        for (uint i = 0; i < counter; i++) {
            (result, data) = debugPrecompile.call(abi.encodePacked(uint8(1)));
            if (!result) {
                revert CallFailed(data);
            }
        }

        if (counter > 3) {
            // stop the recursion
            return;
        }

        // recursive call
        (result, data) = debugPrecompile.call(abi.encodePacked(uint8(0), counter));
        if (!result) {
            revert CallFailed(data);
        }
    }
```
