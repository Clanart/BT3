### Title
Overlapping `BalanceHandler` event windows in nested/recursive precompile calls cause bank balance deltas to be double-applied to the EVM `StateDB` - (File: `precompiles/common/precompile.go`, `precompiles/common/balance_handler.go`)

### Summary
The Ladle report's underlying lesson is that a system relying on an *implicit, unenforced ordering/nesting assumption* (only one "Join Ether" per batch, correct ordering, no re-entry) will silently corrupt state or lock funds once that assumption is violated by any caller the sponsor didn't anticipate. The Cosmos EVM `common.Precompile.runNativeAction` / `BalanceHandler` mechanism makes the analogous implicit assumption: that the window of bank events processed by `BeforeBalanceChange`/`AfterBalanceChange` for one precompile invocation never overlaps with the window processed by another. That assumption breaks under nested/recursive precompile calls sharing the same `sdk.Context`'s `EventManager`.

### Finding Description
`runNativeAction` [1](#0-0)  records `prevEventsLen = len(events)` before running the native action, then after the action completes, re-reads `ctx.EventManager().Events()` and applies `stateDB.AddBalance`/`SubBalance` for every event **from `prevEventsLen` to the current length**, in `BalanceHandler.AfterBalanceChange` [2](#0-1) .

This design implicitly assumes precompile invocations are flat (non-nested) with respect to the shared event log. If a precompile's native action itself triggers another precompile call on the *same* cached context/event manager (e.g. via `EVMKeeper.CallEVMWithData`, or a contract that `call`s/`delegatecall`s into another precompile, or a precompile that internally invokes another module which triggers a nested EVM call), then:

1. Outer call: `BeforeBalanceChange` records `prevLenOuter = N`.
2. Nested call executes and emits new bank events (`CoinSpent`/`CoinReceived`), and if the nested precompile also uses a `BalanceHandler`, its own `BeforeBalanceChange`/`AfterBalanceChange` window (`prevLenInner..M`) applies those deltas to `stateDB` first.
3. Control returns to the outer call. Outer's `AfterBalanceChange` re-fetches `ctx.EventManager().Events()` and processes `events[prevLenOuter:]` — which **still includes** all the events emitted during the nested call, because they share the same underlying `EventManager`.
4. The nested call's bank-balance deltas get applied to `stateDB` a second time by the outer's `AfterBalanceChange`, in addition to whatever the outer's own action produced.

The sponsor's own integration test suite `evmd/tests/integration/balance_handler/balance_handler_test.go` exists specifically to exercise this pattern: it explicitly states the suite "tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB" [3](#0-2) , and it constructs a debug precompile that recursively calls itself via `CallEVMWithData` [4](#0-3) , producing overlapping precompile invocations sharing one event manager, exactly the scenario described above.

The `MaxPrecompileCalls` journal counter [5](#0-4)  and constant of `20` [6](#0-5)  shows the team is aware nested/recursive precompile calls are a supported, reachable pattern (bounded, not forbidden) — but no corresponding fix exists for overlapping `BalanceHandler` windows.

### Impact Explanation
If a real value-bearing precompile (staking `Delegate`/`Undelegate`, distribution, bank/ERC20/werc20/ics20 precompiles) is invoked in a nested fashion — either by a contract making a `call`/`delegatecall` from within a precompile callback, or by any future precompile that internally triggers another EVM call on the same cache context — the resulting `CoinSpent`/`CoinReceived` bank events get double-applied to the EVM `StateDB`. This directly produces:
- Unauthorized duplication of EVM-visible balance for a receiver address (StateDB balance inflated beyond the actual bank-module balance), which the attacker can subsequently transfer/withdraw as spendable EVM value, an irreversible accounting divergence between native bank balances and EVM balances.
- Or double-subtraction from a spender's EVM balance, causing incorrect balance corruption/locking.

This matches the "Critical unauthorized minting/duplication ... of spendable user value across native balances, EVM balances ... or precompile-mediated assets" and "irreversible accounting corruption" impact categories.

### Likelihood Explanation
Likelihood depends on whether an unprivileged user/contract can trigger nested precompile invocations that emit overlapping bank events on the shared context — this is plausible today given (a) contracts can freely `call`/`delegatecall`/`staticcall`/`callcode` into precompiles as shown throughout the `StakingCaller`/`StakingReverter` test contracts [7](#0-6) , and (b) the sponsor's own dedicated test suite exists to probe exactly this nested-call/event-overlap pattern. I could not fully confirm from the available index whether this specific overlapping-window double-application scenario currently causes an assertion failure (i.e., whether it is already patched) or is still open, since the located test only exercises a `BalanceHandler`-using debug precompile that emits a non-bank custom event (`debug_precompile`), not an actual bank `CoinSpent`/`CoinReceived` event, so its assertions don't directly prove the double-apply-of-bank-events path. This should be verified by tracing whether any real value-bearing precompile can be nested in this way and asserting on-chain bank vs. EVM balance parity after such nesting.

### Recommendation
Enforce non-overlapping `BalanceHandler` windows explicitly rather than relying on the implicit flat-invocation assumption, analogous to how the Ladle should have enforced explicit batch-op counters instead of relying on "only the trusted front-end will build correct batches":
- Track a single, non-reentrant balance-processing cursor per `sdk.Context`/`StateDB` (e.g. store `prevEventsLen`/an active-handler marker on the `StateDB` itself, not a fresh per-call struct), so that a nested call's `AfterBalanceChange` "consumes" and advances the shared cursor, and the outer call's subsequent `AfterBalanceChange` only sees events strictly after the nested call's consumed range.
- Alternatively, make `BeforeBalanceChange`/`AfterBalanceChange` reentrancy-safe by explicitly detecting nested invocation (a depth/reentrancy guard already exists conceptually via `precompileCallsCounter`) and skip/merge balance processing for nested calls, applying it only once at the outermost frame.
- Add an integration test using a real bank-event-emitting precompile (not just the debug event) nested inside another precompile call, asserting `stateDB` EVM balance exactly equals the native bank balance after execution.

### Proof of Concept
Conceptual PoC (mirrors the existing debug-precompile test harness, adapted to prove bank-balance double-count):
1. Deploy a "value-bearing" wrapper precompile scenario where a caller contract invokes Precompile A's transaction method; inside Precompile A's native action, before returning, it triggers a call into Precompile B (or recursively into itself) via `EVMKeeper.CallEVMWithData`/nested `call`, and Precompile B's native action performs a bank `SendCoins` (emitting `CoinSpent`/`CoinReceived`).
2. Outer Precompile A's `BeforeBalanceChange` captures `prevLenOuter` before invoking B.
3. B runs its own `BeforeBalanceChange`/`AfterBalanceChange`, applying the `SendCoins` delta to `stateDB` once (correctly).
4. Control returns to A; A's `AfterBalanceChange` re-reads `ctx.EventManager().Events()[prevLenOuter:]`, which still contains B's `CoinSpent`/`CoinReceived` events, and applies the same delta to `stateDB` again.
5. Compare `stateDB.GetBalance(receiver)` vs. `bankKeeper.GetBalance(receiver)` after `NextBlock()` — the EVM-visible balance will be inflated by an extra multiple of the transferred amount relative to the actual bank ledger, which the receiver can spend within the EVM even though the native bank module never backed that duplicated amount.

This exact recursive-invocation harness pattern (via `CallEVMWithData` producing nested `RunNativeAction`/`BalanceHandler` windows on a shared event manager) is already present in `evmd/tests/testdata/debug/debug.go` and `evmd/tests/integration/balance_handler/balance_handler_test.go`, and should be extended to a bank-emitting precompile to conclusively confirm or refute the double-application described above. [8](#0-7)

### Citations

**File:** precompiles/common/precompile.go (L99-123)
```go
	var balanceHandler *BalanceHandler
	if p.BalanceHandlerFactory != nil {
		balanceHandler = p.BalanceHandlerFactory.NewBalanceHandler()
	}

	if balanceHandler != nil {
		balanceHandler.BeforeBalanceChange(ctx)
	}

	bz, err = action(ctx)
	if err != nil {
		return bz, err
	}

	cost := ctx.GasMeter().GasConsumed() - initialGas

	if !contract.UseGas(cost, nil, tracing.GasChangeCallPrecompiledContract) {
		return nil, vm.ErrOutOfGas
	}

	if balanceHandler != nil {
		if err := balanceHandler.AfterBalanceChange(ctx, stateDB); err != nil {
			return nil, err
		}
	}
```

**File:** precompiles/common/balance_handler.go (L68-106)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
		switch event.Type {
		case banktypes.EventTypeCoinSpent:
			spenderAddr, err := ParseAddress(event, banktypes.AttributeKeySpender)
			if err != nil {
				return fmt.Errorf("failed to parse spender address from event %q: %w", banktypes.EventTypeCoinSpent, err)
			}
			if bh.bankKeeper.BlockedAddr(spenderAddr) {
				// Bypass blocked addresses
				continue
			}

			amount, err := ParseAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", banktypes.EventTypeCoinSpent, err)
			}

			stateDB.SubBalance(common.BytesToAddress(spenderAddr.Bytes()), amount, tracing.BalanceChangeUnspecified)

		case banktypes.EventTypeCoinReceived:
			receiverAddr, err := ParseAddress(event, banktypes.AttributeKeyReceiver)
			if err != nil {
				return fmt.Errorf("failed to parse receiver address from event %q: %w", banktypes.EventTypeCoinReceived, err)
			}
			if bh.bankKeeper.BlockedAddr(receiverAddr) {
				// Bypass blocked addresses
				continue
			}

			amount, err := ParseAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", banktypes.EventTypeCoinReceived, err)
			}

			stateDB.AddBalance(common.BytesToAddress(receiverAddr.Bytes()), amount, tracing.BalanceChangeUnspecified)

```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-26)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
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

**File:** evmd/tests/testdata/debug/debug.go (L58-75)
```go
func (p Precompile) Call0(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// data := crypto.Keccak256([]byte("function callback()"))[:4]
	counter := new(big.Int).SetBytes(contract.Input[1:])
	counter = new(big.Int).Add(counter, big.NewInt(1))

	args := math.U256Bytes(counter)
	selector := []byte{0xff, 0x58, 0x5c, 0xaf}
	data := append(selector, args...)

	caller := contract.Caller()
	fmt.Printf("Execute debug precompile %s, %p\n", caller.String(), p.BalanceHandlerFactory)
	rsp, err := p.evmKeeper.CallEVMWithData(ctx, p.Address(), &caller, data, true, nil)
	fmt.Println("callback response:", rsp.Ret, err)
	if err != nil {
		return nil, err
	}
	return nil, nil
}
```

**File:** x/vm/statedb/statedb.go (L436-449)
```go
// AddPrecompileFn adds a precompileCall journal entry
// with a snapshot of the multi-store and events previous
// to the precompile call.
func (s *StateDB) AddPrecompileFn(snapshot int, events sdk.Events) error {
	s.journal.append(precompileCallChange{
		snapshot: snapshot,
		events:   events,
	})
	s.precompileCallsCounter++
	if s.precompileCallsCounter > types.MaxPrecompileCalls {
		return fmt.Errorf("max calls to precompiles (%d) reached", types.MaxPrecompileCalls)
	}
	return nil
}
```

**File:** x/vm/types/call.go (L12-17)
```go
// MaxPrecompileCalls is the maximum number of precompile
// calls within a transaction. We want to limit this because
// for each precompile tx we're creating a cached context
const MaxPrecompileCalls uint8 = 20


```

**File:** precompiles/staking/testdata/StakingCaller.sol (L249-305)
```text
    function testCallUndelegate(
        string memory _validatorAddr,
        uint256 _amount,
        string memory _calltype
    ) public {
        _dequeueUnbondingDelegation();
        address calledContractAddress = staking.STAKING_PRECOMPILE_ADDRESS;
        bytes memory payload = abi.encodeWithSignature(
            "undelegate(address,string,uint256)",
            address(this),
            _validatorAddr,
            _amount
        );
        bytes32 calltypeHash = keccak256(abi.encodePacked(_calltype));

        int64 completionTime = int64(int256(block.timestamp + 21 days));
        if (calltypeHash == keccak256(abi.encodePacked("delegatecall"))) {
            (bool success, bytes memory returnData) = calledContractAddress.delegatecall(payload);
            require(success, "failed delegatecall to precompile");
            completionTime = abi.decode(returnData, (int64));
        } else if (calltypeHash == keccak256(abi.encodePacked("staticcall"))) {
            (bool success, bytes memory returnData) = calledContractAddress.staticcall(payload);
            require(success, "failed staticcall to precompile");
            completionTime = abi.decode(returnData, (int64));
        } else if (calltypeHash == keccak256(abi.encodePacked("call"))) {
            (bool success, bytes memory returnData) = calledContractAddress.call(payload);
            require(success, "failed call to precompile");
            completionTime = abi.decode(returnData, (int64));
        } else if (calltypeHash == keccak256(abi.encodePacked("callcode"))) {
            // NOTE: callcode is deprecated and now only available via inline assembly
            assembly {
            // Load the function signature and argument data onto the stack
                let ptr := add(payload, 0x20)
                let len := mload(payload)

            // Invoke the contract at calledContractAddress in the context of the current contract
            // using CALLCODE opcode and the loaded function signature and argument data
                let success := callcode(
                    gas(),
                    calledContractAddress,
                    0,
                    ptr,
                    len,
                    0,
                    0
                )

            // Check if the call was successful and revert the transaction if it failed
                if iszero(success) {
                    revert(0, 0)
                }
            }
        } else {
            revert("invalid calltype");
        }
        _undelegate(_validatorAddr, _amount, completionTime);
    }
```
