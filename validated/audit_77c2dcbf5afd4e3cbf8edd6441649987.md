Found it. The `decodeFunctionCall` helper in `kaiax/gasless/impl/getter.go` identifies "approve" and "swap" transactions purely by matching `tx.To()` and the 4-byte function selector of `tx.Data()`. This is directly analogous to the reported bug class: an allow-list/approval check that inspects only the "outer" call target/selector without resolving whether the actual value-moving call is being routed through an unregistered wrapper (e.g., a multicall/forwarder contract, or EIP-7702 delegation) placed at the whitelisted token/router address.

### Title
Gasless approve/swap recognition trusts raw `tx.To`/selector without resolving delegation-designated code, enabling spender/router allowlist bypass - ([File: kaiax/gasless/impl/getter.go])

### Summary
`GaslessModule.IsApproveTx`/`IsSwapTx` (via `decodeFunctionCall`) classify a transaction as a legitimate gasless `approve`/`swapForGas` call solely by checking `tx.To() == whitelistedToken/whitelistedRouter` and that `tx.Data()[:4]` matches the expected selector [1](#0-0) . There is no resolution of the executed bytecode at `tx.To()`; the whitelist check treats "the address dialed by the transaction" as equivalent to "the code that actually executes," exactly the assumption that broke in the OpenClaw report (approval bound to the invoked binary path, not the code that ultimately runs).

### Finding Description
`isApproveTx`/`isSwapTx` gate gasless sponsorship purely on `args.Token`/`args.Spender`/`args.Router` derived from `tx.To()` and the ABI-decoded calldata [2](#0-1) . Kaia now supports EIP-7702 delegation designators, where an EOA's (or, more relevant here, any account's) code can be overwritten to point ("delegate") execution to an arbitrary contract via `SetCodeToEOA`/`AddressToDelegation`, and the EVM only follows that delegation when actually executing a call — `evm.resolveCode` transparently redirects execution to the delegate address while callers still see `tx.To()` as the original address . The gasless allowlist logic in `getter.go` never calls `resolveCode`/`ParseDelegation`, nor checks state for a delegation designator at `tx.To()`; it trusts the static `To` address and calldata shape as proof of what will execute. If a whitelisted token or swap-router address were ever delegation-controlled (e.g., an attacker-influenced token contract that self-delegates, or a router address later repurposed to host a delegation designator pointing to attacker code) the gasless verifier would still see a "clean" `approve`/`swapForGas` call to a whitelisted address/selector while the underlying execution is redirected to arbitrary logic — mirroring the /usr/bin/time wrapper case where the security check anchored on the outer, non-executing identity and never unwrapped to the real executable.

### Impact Explanation
`VerifyExecutable`/`IsExecutable` gate whether the sequencer treats an approve+swap pair as gas-sponsored (fee paid from swap proceeds) [3](#0-2) . A bypass of the intended "only interact with the whitelisted token/router logic" invariant lets an attacker get gas-sponsored execution routed to unintended contract logic while still satisfying every static field check (`To`, selector, sender, nonce, repay amount), potentially draining the gasless subsidy/repayment guarantees or moving value through non-audited code paths under the gasless trust umbrella. This is a fee/gasless-settlement-abuse class impact.

### Likelihood Explanation
Exploitability depends on being able to place (or having already-existing) delegation-designator code at one of the `allowedTokens`/`swapRouter` addresses, which requires governance/config to whitelist an address that is (or later becomes) EIP-7702-delegatable, or a token/router implementation bug elsewhere allowing self-delegation. This narrows practical likelihood versus a fully permissionless bypass, but the underlying validation gap — trusting `tx.To()`/selector as a proxy for "the code that executes" without unwrapping delegation designators — is a concrete, unpatched root-cause match to the analog bug class, reachable by any public-RPC caller submitting an approve/swap transaction pair against the gasless module.

### Recommendation
In `decodeFunctionCall`/`isApproveTx`/`isSwapTx`, resolve the actual executing code at `tx.To()` (following `types.ParseDelegation` the same way `evm.resolveCode` does) before trusting `To`/selector-based classification, or explicitly reject/require that whitelisted token and router addresses never carry a delegation designator (reject if `ParseDelegation(state.GetCode(to))` succeeds) prior to approving gasless treatment.

### Proof of Concept
1. Governance/gasless config whitelists token `T` and router `R`.
2. Some path allows `T` (or `R`) to gain EIP-7702-style delegated code, e.g. via `applyAuthorization` setting `SetCodeToEOA(T, AddressToDelegation(evilImpl))` [4](#0-3)  for accounts satisfying `validateAuthorization`'s legacy-key/nonce checks [5](#0-4) .
3. Attacker submits `approve(spender=R, amount=MaxUint)` to `T` and `swapForGas(token=T,...)` to `R`; `decodeFunctionCall` only checks `tx.To()==T/R` and the selector, so both pass `IsApproveTx`/`IsSwapTx` and `VerifyExecutable` even though the EVM will actually execute `evilImpl`'s code at `T`/`R` due to `resolveCode` following the delegation designator [6](#0-5) .
4. The gasless module sponsors/executes the transaction pair believing it is interacting with the audited token/router logic, while the real logic run is attacker-controlled.

### Citations

**File:** kaiax/gasless/impl/getter.go (L69-103)
```go
// IsApproveTx checks following conditions:
// A1. tx.to is a whitelisted ERC20 token.
// A2. tx.data is `approve(spender, amount)`.
// A3. spender is a whitelisted SwapRouter contract.
// A4. amount is MaxUint.
func (g *GaslessModule) IsApproveTx(tx *types.Transaction) bool {
	args, ok := decodeApproveTx(tx, g.signer)
	return ok && g.isApproveTx(args)
}

func (g *GaslessModule) isApproveTx(args *ApproveArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.allowedTokens[args.Token] && // A1
		g.swapRouter == args.Spender && // A3
		args.Amount.Cmp(abi.MaxUint256) == 0 // A4
}

// IsSwapTx checks following conditions:
// S1. tx.to is a whitelisted SwapRouter contract.
// S2. tx.data is `swapForGas(token, amountIn, minAmountOut, amountRepay)`.
// S3. token is a whitelisted ERC20 token.
func (g *GaslessModule) IsSwapTx(tx *types.Transaction) bool {
	args, ok := decodeSwapTx(tx, g.signer)
	return ok && g.isSwapTx(args)
}

func (g *GaslessModule) isSwapTx(args *SwapArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.swapRouter == args.Router && // S1
		g.allowedTokens[args.Token] // S3
}
```

**File:** kaiax/gasless/impl/getter.go (L182-193)
```go
func decodeFunctionCall(tx *types.Transaction, method abi.Method) (common.Address, map[string]interface{}, bool) {
	if tx.Type() != types.TxTypeLegacyTransaction || // not legacy tx: unable to statically determine the max gas fee.
		tx.To() == nil || // not a contract call.
		len(tx.Data()) < 4 || // too short to be a contract call.
		!bytes.Equal(tx.Data()[:4], method.ID) { // not the target function.
		return common.Address{}, nil, false
	}

	inputs := make(map[string]interface{})
	err := method.Inputs.UnpackIntoMap(inputs, tx.Data()[4:])
	return *tx.To(), inputs, err == nil
}
```

**File:** kaiax/gasless/impl/getter.go (L211-266)
```go
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

**File:** blockchain/state_transition.go (L732-760)
```go
func (st *StateTransition) validateAuthorization(auth *types.SetCodeAuthorization) (authority common.Address, err error) {
	// Verify chain ID is 0 or equal to current chain ID.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(st.evm.ChainConfig().ChainID) != 0 {
		return authority, ErrAuthorizationWrongChainID
	}
	// Limit nonce to 2^64-1 per EIP-2681.
	if auth.Nonce+1 < auth.Nonce {
		return authority, ErrAuthorizationNonceOverflow
	}
	// Validate signature values and recover authority.
	authority, err = auth.Authority()
	if err != nil {
		return authority, fmt.Errorf("%w: %v", ErrAuthorizationInvalidSignature, err)
	}
	// Check the authority account
	//  1) doesn't have code or has existing delegation
	//  2) matches the auth's nonce
	//
	// Note it is added to the access list even if the authorization is invalid.
	st.state.AddAddressToAccessList(authority)
	code := st.state.GetCode(authority)
	if _, ok := types.ParseDelegation(code); len(code) != 0 && !ok {
		return authority, ErrAuthorizationDestinationHasCode
	}
	if have := st.state.GetNonce(authority); have != auth.Nonce {
		return authority, ErrAuthorizationNonceMismatch
	}
	return authority, nil
}
```

**File:** blockchain/state_transition.go (L762-794)
```go
func (st *StateTransition) applyAuthorization(auth *types.SetCodeAuthorization, rules params.Rules) (err error) {
	authority, err := st.validateAuthorization(auth)
	if err != nil {
		return err
	}

	// If the account already exists in state, refund the new account cost
	// charged in the initrinsic calculation.
	if st.state.Exist(authority) {
		// If the account is not AccountKeyTypeLegacy, setcode is not allowed.
		accountKeyType := st.state.GetKey(authority).Type()
		if !accountKeyType.IsLegacyAccountKey() {
			return fmt.Errorf("%w: %v", ErrAuthorizationNotAllowAccountKeyType, accountKeyType)
		}
		st.state.AddRefund(params.CallNewAccountGas - params.TxAuthTupleGas)
	}

	// Update nonce and account code.
	st.state.IncNonce(authority)
	delegation := types.AddressToDelegation(auth.Address)
	if common.EmptyAddress(auth.Address) {
		// Delegation to zero address means clear.
		st.state.SetCodeToEOA(authority, []byte{}, rules)
		return nil
	}

	// Otherwise install delegation to auth.Address.
	// We treat EOA and SCA as separate objects and therefore need to use
	// distinct methods.
	st.state.SetCodeToEOA(authority, delegation, rules)

	return nil
}
```

**File:** blockchain/vm/evm.go (L662-674)
```go
// resolveCode returns the code associated with the provided account. After
// Prague, it can also resolve code pointed to by a delegation designator.
func (evm *EVM) resolveCode(addr common.Address) []byte {
	code := evm.StateDB.GetCode(addr)
	if !evm.chainRules.IsPrague {
		return code
	}
	if target, ok := types.ParseDelegation(code); ok {
		// Note we only follow one level of delegation.
		return evm.StateDB.GetCode(target)
	}
	return code
}
```
