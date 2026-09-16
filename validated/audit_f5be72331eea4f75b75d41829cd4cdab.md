## Title
GaslessSwapRouter's empty `receive()` silently absorbs KAIA and lets the owner sweep it via `claimCommission()` - (File: contracts/bindings/kip247/GaslessSwapRouter.go)

## Summary
`GaslessSwapRouter` (the KIP-247 gasless-swap router contract) is deployed with a bare `receive() external payable` that has no logic [1](#0-0) . Its compiled runtime bytecode confirms this: the dispatcher prologue treats a zero-length calldata call (plain KAIA transfer) as a no-op `STOP`, while short-but-nonzero calldata reverts and only calldata ≥4 bytes reaches the function selector dispatch [2](#0-1) . This is the exact bytecode signature of an empty `receive(){}` function with no `fallback()` — any user who sends KAIA directly to this contract (e.g. by mistake, or via a wallet resolving the router address) has that value silently accepted into the contract with zero accounting, no event, and no way to reclaim it.

## Finding Description
The contract exposes `claimCommission()`, which is owner-only and sweeps the **entire** native balance of the contract (`SELFBALANCE`/`self.balance`, opcode `47`) to the owner in one shot, emitting `CommissionClaimed` [3](#0-2) [2](#0-1) . There is no per-user or per-swap commission ledger that separates "true" commission proceeds accumulated through `swapForGas` from arbitrary KAIA that lands in the contract via the empty `receive()`. Consequently:
- A user (unprivileged transaction sender) who accidentally sends KAIA directly to the `GaslessSwapRouter` address — instead of calling `swapForGas` with the correct token/amount parameters — has that value swallowed by the no-op `receive()`.
- That value becomes indistinguishable from legitimate commission funds already in the contract, and the router owner can withdraw it in full via `claimCommission()`.

This mirrors the referenced UXD `UXDController` finding precisely: a `receive()` function that performs no bookkeeping means any ETH/KAIA sent without an explicit function call is effectively lost to the sender, and here it is worse because a privileged party (the owner) can extract the misdirected funds as if they were commission.

## Impact Explanation
Any KAIA an unprivileged user accidentally sends to the `GaslessSwapRouter` contract address is unrecoverable by that user and becomes claimable by the contract owner through `claimCommission()`. This is a concrete unauthorized value movement/fee-abuse scenario reachable by a single transaction from any external account, fitting the "gasless module" and "fee-delegation abuse" categories called out as in-scope. Severity is Medium: it requires user error (sending value with no calldata to the router) rather than exploitation of the router's core swap logic, but the loss is real, immediate, and irreversible once the owner calls `claimCommission()`.

## Likelihood Explanation
Likelihood is moderate: it depends on a user mistakenly sending KAIA directly to the router contract (no calldata) rather than invoking `swapForGas`/other entry points. This can happen through UI/wallet misconfiguration, copy-pasted contract addresses, or attempts to "fund" the router thinking it needs pre-funding for gas swaps. Because `GaslessSwapRouter` is a public-facing contract address that end-users interact with as part of Kaia's gasless transaction flow, accidental direct transfers are a realistic occurrence.

## Recommendation
Remove the empty `receive()` (and any implicit fallback) so that plain-value transfers without a matching function call revert, forcing users to interact only through well-defined, accounted-for entry points (e.g., `swapForGas`). If the router is intended to hold native KAIA balances between swaps, track incoups explicitly (e.g., increment a `pendingRefunds`/`commissionAccrued` state variable inside relevant functions) instead of relying on `address(this).balance`, and have `claimCommission()` only transfer the tracked commission amount rather than the entire contract balance.

## Proof of Concept
1. Owner deploys `GaslessSwapRouter` with a WKAIA address (`DeployGaslessSwapRouter`) [4](#0-3) .
2. A user, believing they are funding/interacting with the router, sends a plain KAIA transfer (empty calldata) to the router's address instead of calling `SwapForGas`. Per the ABI/bytecode, this resolves to `receive()`, which performs no state update [5](#0-4) .
3. The router's native balance now includes the user's mistakenly sent KAIA, indistinguishable from swap commissions.
4. The owner calls `ClaimCommission()`, which transfers the router's full balance (including the user's misdirected funds) to the owner and emits `CommissionClaimed` [3](#0-2) .
5. The user has no function to recover their KAIA; it has been redirected to the owner.

Note: I was unable to inspect the original Solidity source (`GaslessSwapRouter.sol`) directly — only the generated Go bindings and decompiled bytecode are indexed in this repo — so the exact wording of the `receive()`/`claimCommission()` implementation is inferred from the ABI and disassembled bytecode rather than literal source review. If precise source-level confirmation is needed, a full Devin session with repository/file access would be required to pull the actual `.sol` file.

### Citations

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L42-43)
```go
// GaslessSwapRouterBinRuntime is the compiled bytecode used for adding genesis block without deploying code.
const GaslessSwapRouterBinRuntime = `60406080815260048036101561001f575b5050361561001d57600080fd5b005b600091823560e01c8062fa3d50146112ba578063145d51d814611276578063161efb62146111d95780635ea1d6f8146111ba5780635fa7b58414611071578063632db21c14610f12578063715018a614610eac57806375151b6314610e6357806380426901146108915780638da5cb5b1461086b578063c6e85b3b1461039e578063d3c7c2c714610302578063e3bcccb4146102ad578063f2fde38b146101c65763fad99f98146100d05750610010565b346101c257826003193601126101c2576100e86115d5565b479182156101805783808080866001600160a01b038254165af161010a61157a565b501561013e57507f812744101ebaaf6b793a9a3057b00dff294aa41e3665594c617fc101fb0387dc9160209151908152a180f35b6020606492519162461bcd60e51b8352820152601560248201527f436f6d6d697373696f6e436c61696d4661696c656400000000000000000000006044820 ... (truncated)
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L49-64)
```go
// DeployGaslessSwapRouter deploys a new Kaia contract, binding an instance of GaslessSwapRouter to it.
func DeployGaslessSwapRouter(auth *bind.TransactOpts, backend bind.ContractBackend, _wkaia common.Address) (common.Address, *types.Transaction, *GaslessSwapRouter, error) {
	parsed, err := GaslessSwapRouterMetaData.GetAbi()
	if err != nil {
		return common.Address{}, nil, nil, err
	}
	if parsed == nil {
		return common.Address{}, nil, nil, errors.New("GetABI returned nil")
	}

	address, tx, contract, err := bind.DeployContract(auth, *parsed, common.FromHex(GaslessSwapRouterBin), backend, _wkaia)
	if err != nil {
		return common.Address{}, nil, nil, err
	}
	return address, tx, &GaslessSwapRouter{GaslessSwapRouterCaller: GaslessSwapRouterCaller{contract: contract}, GaslessSwapRouterTransactor: GaslessSwapRouterTransactor{contract: contract}, GaslessSwapRouterFilterer: GaslessSwapRouterFilterer{contract: contract}}, nil
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L491-503)
```go
// ClaimCommission is a paid mutator transaction binding the contract method 0xfad99f98.
//
// Solidity: function claimCommission() returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) ClaimCommission(opts *bind.TransactOpts) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "claimCommission")
}

// ClaimCommission is a paid mutator transaction binding the contract method 0xfad99f98.
//
// Solidity: function claimCommission() returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) ClaimCommission() (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.ClaimCommission(&_GaslessSwapRouter.TransactOpts)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L617-629)
```go
// Receive is a paid mutator transaction binding the contract receive function.
//
// Solidity: receive() payable returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) Receive(opts *bind.TransactOpts) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.RawTransact(opts, nil) // calldata is disallowed for receive function
}

// Receive is a paid mutator transaction binding the contract receive function.
//
// Solidity: receive() payable returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) Receive() (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.Receive(&_GaslessSwapRouter.TransactOpts)
}
```
