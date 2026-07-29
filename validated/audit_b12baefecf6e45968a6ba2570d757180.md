### Title
Critical Balance Desync in Recursive Precompile Calls - ([File: precompiles/common/balance_handler.go])

### Summary
A critical vulnerability exists in the `BalanceHandler` logic used by stateful precompiles. When a precompile makes a recursive call to another precompile (or back to itself via a contract), the shared `BalanceHandler` instance's `prevEventsLen` state is overwritten. This causes the handler to miscalculate the range of events to process, leading to double-counting or skipping of balance updates between the native Cosmos SDK bank module and the EVM `StateDB`.

### Finding Description
The Cosmos EVM uses a `BalanceHandler` to synchronize balance changes emitted as Cosmos SDK events (e.g., `bank.MsgSend`) back into the EVM `StateDB` during precompile execution.

In `precompiles/common/balance_handler.go`:
1. `BeforeBalanceChange` records the current number of events: `bh.prevEventsLen = len(ctx.EventManager().Events())` [1](#0-0) .
2. `AfterBalanceChange` iterates from that index to the end: `for _, event := range events[bh.prevEventsLen:]` [2](#0-1) .

The vulnerability occurs because precompiles are registered as singletons in the `EVMKeeper`. When a precompile execution triggers a recursive call (e.g., Precompile A -> Contract B -> Precompile A), both calls operate on the same `BalanceHandler` instance. 

**Trace of failure:**
1. **Call 1** starts: `BeforeBalanceChange` sets `prevEventsLen = 10`.
2. **Call 1** executes logic, emitting 2 events (Total: 12).
3. **Call 1** triggers **Call 2** (Recursive):
   - **Call 2** `BeforeBalanceChange` sets `prevEventsLen = 12` (overwriting the `10` from Call 1).
   - **Call 2** executes, emitting 2 events (Total: 14).
   - **Call 2** `AfterBalanceChange` processes events `[12:14]`. Correct for Call 2.
4. **Call 1** resumes:
   - **Call 1** `AfterBalanceChange` processes events `[12:14]` (using the overwritten `prevEventsLen`).
   - **Result**: Call 1's original events `[10:12]` are **never processed** in the `StateDB`, while Call 2's events `[12:14]` are **processed twice**.

This leads to a permanent divergence between the Bank module (native) and `StateDB` (EVM) balances.

### Impact Explanation
This is a **Critical** impact vulnerability. It allows for:
1. **Unauthorized Minting/Theft**: An attacker can use recursive calls to make the `StateDB` "forget" a `SubBalance` call (spending tokens in Bank but not EVM) or double-count an `AddBalance` call (receiving tokens once in Bank but twice in EVM).
2. **Accounting Corruption**: Irreversible desync between the two primary balance stores of the chain.
3. **AppHash Divergence**: Since the `StateDB` commit is based on these corrupted balances, it will lead to consensus failures or permanent state corruption that an unprivileged user can trigger via standard contract interactions.

### Likelihood Explanation
The likelihood is high for any chain enabling stateful precompiles (like Staking, Distribution, or ICS20) that can be called via EVM contracts. The codebase contains integration tests explicitly documenting this "balance handler bug" [3](#0-2) , confirming the reachability and known nature of the flaw in the current architecture.

### Recommendation
The `BalanceHandler` should not store `prevEventsLen` as a member variable if the precompile is a singleton. Instead:
1. Pass the `prevEventsLen` through the call stack (e.g., as a return value from `BeforeBalanceChange` to be passed into `AfterBalanceChange`).
2. Ensure each execution context (even recursive ones) uses a unique state or stack-allocated variable to track its event offset.
3. Alternatively, instantiate a new `BalanceHandler` for every `Run` call instead of using a factory that might return a shared instance.

### Proof of Concept
A contract can trigger this by calling the `ICS20` or `Staking` precompile, then having the recipient (if it's a contract) or a hook call back into the same precompile.
```solidity
interface IPrecompile {
    function execute() external;
}

contract Attacker {
    IPrecompile constant TARGET = IPrecompile(0x...);
    bool recursive = false;

    function attack() external {
        TARGET.execute(); // First call
    }

    // Called if TARGET.execute() triggers a callback to this contract
    fallback() external {
        if (!recursive) {
            recursive = true;
            TARGET.execute(); // Recursive call overwrites prevEventsLen
        }
    }
}
```
The first `execute()` will fail to sync its Bank events to the EVM `StateDB` because its `prevEventsLen` was moved forward by the second call.

### Citations

**File:** precompiles/common/balance_handler.go (L46-48)
```go
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L71-71)
```go
	for _, event := range events[bh.prevEventsLen:] {
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```
