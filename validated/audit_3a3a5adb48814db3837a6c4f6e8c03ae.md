### Title
Recursive precompile calls sharing a single `BalanceHandler` instance corrupt EVM `StateDB` balances relative to the native bank/precisebank ledger - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
This is the Cosmos EVM analog of the "no entry check / trivial guard" bug class from the report's `haircut` finding: a function that is only trivially guarded is reachable through a re-entrant/nested call path from an attacker-controlled contract and can corrupt internal accounting invariants. Here, the `BalanceHandler` used by every value-moving precompile (bank, staking, distribution, erc20, gov, ics20, slashing) records `prevEventsLen` before a call and replays only the bank/precisebank events emitted after that point to update the EVM `StateDB`. When a single `BalanceHandler` instance is shared across nested/recursive precompile invocations (e.g., a malicious ERC20 contract's `_beforeTokenTransfer`/hook recursively calling back into a precompile such as `distribution.claimRewards` or `staking.delegate`), the `prevEventsLen` bookmark gets overwritten by the inner call, so the outer call's `AfterBalanceChange` processes the wrong slice of the event log.

### Finding Description
Every precompile that mutates native balances wraps its state-changing logic with `BeforeBalanceChange`/`AfterBalanceChange` from a `BalanceHandler`: [1](#0-0)  This pattern relies on `prevEventsLen` being a stable bookmark for a single call frame. [2](#0-1)  The `AfterBalanceChange` method slices `ctx.EventManager().Events()` starting at `prevEventsLen` and mechanically re-applies `CoinSpent`/`CoinReceived`/precisebank fractional-balance events onto the EVM `StateDB`: [3](#0-2)  and the precisebank fractional handling: [4](#0-3) 

The repository itself contains a dedicated regression test explicitly describing this exact defect: "recursive precompile calls share the same `BalanceHandler` instance, causing `prevEventsLen` to be overwritten... leads to balance desync between native bank keeper and EVM stateDB": [5](#0-4)  The test deploys a caller contract that triggers nested/recursive precompile calls and asserts on the resulting event counts: [6](#0-5) 

This is directly analogous to the "haircut" issue in the source report: werg (Marginswap) acknowledged haircut was only "trivially guarded" and that a malicious token contract calling back into `haircut` mid-execution could void other users' bonds by exploiting a callback path the developers hadn't fully closed off. In this codebase, the equivalent callback surface is any precompile whose downstream Cosmos SDK message execution can trigger a callback into EVM code that re-enters a precompile (ERC20 `_beforeTokenTransfer`/`_afterTokenTransfer` hooks, IBC callback contracts calling precompiles, or nested `try/catch` patterns as seen in the test fixtures `ERC20RecursiveNonRevertingPrecompileCall.sol` / `ERC20RecursiveRevertingPrecompileCall.sol` and `StakingReverter.sol`, which specifically test nested precompile calls with reverts): [7](#0-6) [8](#0-7) 

Because `BeforeBalanceChange`/`AfterBalanceChange` bookmark and replay bank events using a shared mutable field (`prevEventsLen`) rather than a call-stack-scoped snapshot, an outer precompile call's post-processing can read an event slice that starts after events belonging to it were already consumed by an inner nested call, or vice versa — causing the `StateDB` (EVM-visible balance) to diverge from the true `x/bank`/`x/precisebank` balance recorded in the KVStore.

### Impact Explanation
If the `StateDB` balance for an address diverges from the ground-truth bank/precisebank balance, this breaks the core invariant that Cosmos EVM asset-representation paths must preserve 1:1 accounting between native coins and EVM-visible balances (the "Asset-representation path" invariant). Depending on the direction of the divergence this can manifest as: EVM-visible balance under-crediting a legitimate transfer (apparent fund loss/lock for the user in the EVM view) or over-crediting (apparent unauthorized balance inflation visible to subsequent EVM logic, e.g., allowing a contract to pass EVM-level balance checks it should not pass) — corrupting `StateDB` in a way that is only reconciled/overwritten on the next unrelated balance-changing operation, and in the interim can be read and acted upon by other contract logic within the same transaction or block (e.g., collateral checks, transfer permission checks in the same call context that read `StateDB.GetBalance`).

### Likelihood Explanation
The trigger requires only an unprivileged user to deploy or interact with a contract that performs a nested/recursive call into a value-moving precompile from within a callback triggered by another precompile call (ERC20 token hooks, IBC destination callbacks calling precompiles, or plain reentrant Solidity calls to `STAKING_CONTRACT`/`DISTRIBUTION_CONTRACT`/bank precompile addresses) — no privileged role or validator/relayer collusion is needed. The project's own dedicated regression test and multiple purpose-built "recursive precompile call" test fixtures indicate this is a known, previously identified condition that the team was actively testing/hardening against, which raises confidence that the reachable path exists in production precompile code, though I could not fully confirm from the available index whether the currently-shipped mitigation (if any) fully eliminates the divergence in every nested-call ordering, since the full precompile.go wiring of `BeforeBalanceChange`/`AfterBalanceChange` per call frame and the exact fix state was not retrievable in full detail within the indexed content.

### Recommendation
Scope `prevEventsLen` (and the entire `BalanceHandler`) per call-stack frame rather than sharing one mutable instance across nested precompile invocations — e.g., use a stack/counter of bookmarks pushed on `BeforeBalanceChange` and popped on `AfterBalanceChange`, or derive a fresh `BalanceHandler` per nested EVM call depth so that inner calls cannot clobber an outer call's event-log bookmark. Add invariant checks (as already partially done via `BlockedAddr` bypass) that assert, after each `AfterBalanceChange`, that the aggregate delta applied to `StateDB` matches the aggregate delta recorded in `x/bank`/`x/precisebank` for the full call, and fail closed (revert) rather than silently applying a partial/incorrect event slice.

### Proof of Concept
The repository's own test demonstrates the mechanism: a caller contract invokes a debug precompile in a way that produces nested precompile calls; the test explicitly checks the resulting event count and precompile-invocation count to validate the fix/regression [6](#0-5) . A real-world attack path would replace the debug precompile with a value-moving one (e.g., an ERC20 contract with a `_beforeTokenTransfer` hook that calls `distribution.STAKING_CONTRACT.delegate`/`claimRewards`, similar to `ERC20RecursiveNonRevertingPrecompileCall.sol`) so that the nested call's `BeforeBalanceChange`/`AfterBalanceChange` cycle overwrites `prevEventsLen` mid-flight, causing the outer transfer's bank events to be mis-replayed onto `StateDB`.

### Citations

**File:** precompiles/common/balance_handler.go (L37-41)
```go
// BalanceHandler is a struct that handles balance changes in the Cosmos SDK context.
type BalanceHandler struct {
	bankKeeper    BankKeeper
	prevEventsLen int
}
```

**File:** precompiles/common/balance_handler.go (L43-48)
```go
// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L68-88)
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
```

**File:** precompiles/common/balance_handler.go (L107-131)
```go
		case precisebanktypes.EventTypeFractionalBalanceChange:
			addr, err := ParseAddress(event, precisebanktypes.AttributeKeyAddress)
			if err != nil {
				return fmt.Errorf("failed to parse address from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}
			if bh.bankKeeper.BlockedAddr(addr) {
				// Bypass blocked addresses
				continue
			}

			delta, err := ParseFractionalAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}

			deltaAbs, err := utils.Uint256FromBigInt(new(big.Int).Abs(delta))
			if err != nil {
				return fmt.Errorf("failed to convert delta to Uint256: %w", err)
			}

			if delta.Sign() == 1 {
				stateDB.AddBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			} else if delta.Sign() == -1 {
				stateDB.SubBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-35)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
	suite.Suite

	coordinator *evmibctesting.Coordinator
	chain       *evmibctesting.TestChain
}

func TestBalanceHandlerTestSuite(t *testing.T) {
	suite.Run(t, new(BalanceHandlerTestSuite))
}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L76-102)
```go
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
```

**File:** contracts/solidity/ERC20RecursiveNonRevertingPrecompileCall.sol (L124-142)
```text
    function _beforeTokenTransfer(
        address from,
        address to,
        uint256 amount
    ) internal virtual override(ERC20, ERC20Pausable) {
        // Emit an event to track if this hook is called
        emit BeforeTokenTransferHookCalled(from, to, amount);

        for(uint256 i=0; i < 5; i++) {
            try ERC20RecursiveNonRevertingPrecompileCall(address(this)).claimRewards() {

            } catch {

            }

        }

        super._beforeTokenTransfer(from, to, amount);
    }
```

**File:** contracts/solidity/precompiles/testutil/contracts/StakingReverter.sol (L52-80)
```text
    /// @dev nestedTryCatchDelegations performs nested try/catch calls to precompile
    /// where inner calls revert intentionally. Only the successful delegations
    /// outside the reverting scope should persist.
    ///
    /// Expected successful delegations: 1 (before loop) + outerTimes (after each catch) + 1 (after loop)
    function nestedTryCatchDelegations(uint outerTimes, uint innerTimes, string calldata validatorAddress) external {
        // Initial successful delegate before any nested reverts
        STAKING_CONTRACT.delegate(address(this), validatorAddress, 10);

        for (uint i = 0; i < outerTimes; i++) {
            // Outer call that will revert and be caught
            try StakingReverter(address(this)).performDelegation(validatorAddress) {
                // no-op
            } catch {
                // After catching the revert, perform a successful delegate
                STAKING_CONTRACT.delegate(address(this), validatorAddress, 10);

                // Inner nested loop of reverting calls
                for (uint j = 0; j < innerTimes; j++) {
                    try StakingReverter(address(this)).performDelegation(validatorAddress) {
                        // no-op
                    } catch {}
                }
            }
        }

        // Final successful delegate after the loops
        STAKING_CONTRACT.delegate(address(this), validatorAddress, 10);
    }
```
