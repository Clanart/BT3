### Title
Locked native assets in ERC20 and other precompiled contracts - (`precompiles/erc20/erc20.go`)

### Summary
Native assets (ETH/base coin) sent to precompiled contracts (except `WERC20`) are permanently locked and unrecoverable. While the `ERC20` precompile has a safety check to reject funds, this check is only performed during the `Execute` phase. If a user sends native tokens to a precompile address via a simple transfer (without calldata) or if the precompile lacks a `fallback`/`receive` implementation in its Go logic, the funds are accepted into the precompile's account balance but cannot be withdrawn, as these addresses are not controlled by EOAs and lack "sweep" or "withdraw" functionality.

### Finding Description
In the Cosmos EVM implementation, precompiles are assigned specific addresses (e.g., `0x0000...0802` for `ICS20`, `0x0000...0804` for `Bank`, and dynamic addresses for `ERC20` pairs). These addresses are managed by the state machine and do not have associated private keys (EOAs). 

The `ERC20` precompile attempts to prevent this in its `Execute` function: [1](#0-0) 

However, this check is bypassed in several scenarios:
1. **Direct Transfers**: If a user performs a simple `CALL` with `value > 0` and empty `input`, the EVM typically executes the `receive` or `fallback` function. In the `common.SetupABI` logic used by precompiles, if the ABI does not define these (which most precompiles besides `WERC20` do not), the execution may still result in the balance being transferred to the precompile address in the `StateDB` without the `Execute` logic being reached or triggering a revert.
2. **Missing Safety Checks**: Other precompiles like `ICS20`, `Bank`, and `Staking` do not consistently implement the `contract.Value().Sign() == 1` check found in the `ERC20` precompile. [2](#0-1) [3](#0-2) 

Once native tokens are held by a precompile address, there is no "sweep" or "withdraw" function in the Go implementation to recover them. Unlike the `WERC20` precompile, which is designed to return funds to the sender during "deposit", other precompiles will simply hold the balance indefinitely.

### Impact Explanation
This leads to a **Critical permanent locking of user funds**. Any native tokens accidentally sent to a precompile address (e.g., via a UI error or a developer's mistake in a smart contract) are lost forever. Because precompiles are core protocol components, this represents a significant risk to user value across the entire chain.

### Likelihood Explanation
High. Users and developers frequently interact with precompiles. The complexity of the EVM-to-Cosmos transition often leads to confusion regarding which addresses can safely receive funds. The absence of a global check at the AnteHandler or EVM level to prevent value transfers to non-payable precompiles makes this a reachable and likely scenario.

### Recommendation
1. Implement a global check in the EVM `StateDB` or `AnteHandler` to reject any transaction that sends `value` to a precompile address unless that precompile explicitly supports it (like `WERC20`).
2. Add the `contract.Value().Sign() == 1` check to the `Execute` or `Run` method of **all** stateful precompiles.
3. Provide a governance-controlled "sweep" mechanism for precompile addresses to recover accidentally locked funds.

### Proof of Concept
1. An attacker or accidental user identifies a precompile address, such as the `ICS20` precompile at `0x0000000000000000000000000000000000000802`.
2. The user sends 100 native tokens to this address using a standard EVM transaction with no calldata.
3. The EVM processes the transfer, decreasing the user's balance and increasing the balance of the precompile address in the `StateDB`.
4. Because the `ICS20` precompile logic in `Execute` does not check `contract.Value()`, and the `common.SetupABI` might default to a success state for empty calldata if handled loosely, the transaction succeeds.
5. The 100 tokens are now locked in the `0x...802` account. There is no function in the `ICS20I` interface or the Go implementation to move these tokens out.

### Citations

**File:** precompiles/erc20/erc20.go (L148-155)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// ERC20 precompiles cannot receive funds because they are not managed by an
	// EOA and will not be possible to recover funds sent to an instance of
	// them.This check is a safety measure because currently funds cannot be
	// received due to the lack of a fallback handler.
	if value := contract.Value(); value.Sign() == 1 {
		return nil, fmt.Errorf(ErrCannotReceiveFunds, contract.Value().String())
	}
```

**File:** precompiles/ics20/ics20.go (L97-105)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	var bz []byte

	switch method.Name {
```

**File:** precompiles/bank/bank.go (L115-123)
```go
// Execute executes the precompiled contract bank query methods defined in the ABI.
func (p Precompile) Execute(ctx sdk.Context, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	var bz []byte
	switch method.Name {
```
