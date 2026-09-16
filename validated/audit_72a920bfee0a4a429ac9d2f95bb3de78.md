### Title
EIP-7702 authorization delegation targets bypass the precompiled-address protection enforced on `to` - (File: blockchain/state_transition.go)

### Summary
Kaia enforces "cannot target a precompiled contract address" as a security invariant on the `to`/`Recipient` field of every transaction type, including `TxTypeEthereumSetCode` (`TxInternalDataEthereumSetCode.Validate`), and again at the EVM `Call`/`create` layer (`common.IsPrecompiledContractAddress`). However, EIP-7702 introduces a second, functionally equivalent way to point an address at behavior: `AuthorizationList[].Address`, which makes an EOA delegate its code to an arbitrary target address via `types.AddressToDelegation`. The authorization-processing path (`validateAuthorization` / `applyAuthorization`) never calls `common.IsPrecompiledContractAddress` on `auth.Address`, so the same class of restriction that is enforced for `to` is silently skipped for the delegation target — exactly the "checks the legacy field but not the equivalent new field" pattern described in the Portainer advisory (bind checks only `HostConfig.Binds`, not the equivalent `HostConfig.Mounts`).

### Finding Description
- `TxInternalDataEthereumSetCode.Validate` guards the call-target field: [1](#0-0) 
- The actual EIP-7702 authorization application logic in `StateTransition.validateAuthorization` / `applyAuthorization` recovers the `authority`, checks chain ID, nonce, and existing code/account-key type, but never checks whether `auth.Address` (the delegation target) is a precompiled address: [2](#0-1) 
- Elsewhere in the codebase, `common.IsPrecompiledContractAddress` is treated as a hard invariant that must be checked at every "make this address the destination of execution" site — `evm.Call`, `evm.create`, and every `TxInternalData*.Validate()` implementation (value transfer, smart-contract deploy, etc.) all call it: [3](#0-2) [4](#0-3) 

  `AuthorizationList[].Address` is the delegation-equivalent of `to`: once applied, `types.AddressToDelegation(auth.Address)` is written as the authority's code, and subsequent calls to the authority resolve through `types.ParseDelegation` to execute at `auth.Address`: [5](#0-4) [6](#0-5) 

  Because no precompile check exists on this path, any regular sender can submit an EIP-7702 `SetCodeTx` with an authorization whose `Address` field equals a precompiled address (e.g. `0x01`–`0x0400` range, or a Kaia system-contract address in that range), causing their EOA (or any co-signing authority's EOA) to be delegated to that address — a state that the rest of the codebase treats as impossible/invalid by construction (every other code path that could produce this state is explicitly blocked).

### Impact Explanation
This breaks an invariant that the rest of the state-transition and EVM code relies on: that no account other than a properly-created precompiled-contract account object can have its code point at (or effectively execute as) a precompiled address. Concretely:
- `evm.Call`'s precompiled-address guard (`kerrors.ErrPrecompiledContractAddress`, lines 279–289) is bypassed for delegated calls, since delegation resolution happens via the EOA's address, not the precompile's address — the EVM only re-checks the precompile-address range when `addr` itself equals the precompile, not when an EOA is merely delegated to it. This can produce state divergence between honest nodes if any downstream logic (access-list warming, gas-metering, precompile activation map lookups) treats a delegated EOA differently than intended, and it certainly represents "acceptance of an invalid transaction" — the network is defined to reject precompile-address targeting via every other transaction type/field, but silently accepts it via `AuthorizationList`.
- Because `SetCodeToEOA` is invoked unconditionally once `validateAuthorization` succeeds, and `validateAuthorization` performs no precompile check, this is a state-transition-level acceptance of an invalid delegation that should have been rejected — matching the "acceptance of an invalid transaction" criterion.

### Likelihood Explanation
Any authenticated, unprivileged sender able to submit an `TxTypeEthereumSetCode` transaction (Prague-activated chains) can trigger this by constructing a `SetCodeAuthorization` whose `Address` is set to any address in the `0x01`–`0x0400` precompile range and self-signing it as `authority`. No special privileges, governance parameters, or race conditions are required — a single transaction is sufficient, matching the "single submitted transaction" reachability bar.

### Recommendation
Add a `common.IsPrecompiledContractAddress(auth.Address, rules)` check inside `StateTransition.validateAuthorization` (or `applyAuthorization`) in `blockchain/state_transition.go`, returning a rejection (e.g. reusing/extending `kerrors.ErrPrecompiledContractAddress`) before installing the delegation, mirroring the checks already present on `t.Recipient` in `TxInternalDataEthereumSetCode.Validate` and in `evm.Call`/`evm.create`.

### Proof of Concept
1. On a Prague-activated Kaia network, craft `SetCodeAuthorization{ChainID: <chain>, Address: common.HexToAddress("0x0000000000000000000000000000000000000001"), Nonce: <authority's current nonce>}` and sign it with the authority's key via `types.SignSetCode`.
2. Build a `TxTypeEthereumSetCode` transaction (`to` set to any non-precompiled address to pass `TxInternalDataEthereumSetCode.Validate`) with `AuthorizationList` containing the above authorization, and submit it via the public RPC (`eth_sendRawTransaction`).
3. In `StateTransition.TransitionDb` → `applyAuthorization`, observe that `validateAuthorization` (blockchain/state_transition.go:732-760) performs no `IsPrecompiledContractAddress` check on `auth.Address`, so `applyAuthorization` proceeds to call `st.state.SetCodeToEOA(authority, types.AddressToDelegation(auth.Address), rules)`, writing delegation code `0xef0100<precompile-address>` to the authority account — a state that no other transaction path in the codebase is able to produce, since every other `Validate()` implementation and `evm.create`/`evm.Call` reject a precompiled destination.

### Citations

**File:** blockchain/types/tx_internal_data_ethereum_set_code.go (L35-52)
```go
// DelegationPrefix is used by code to denote the account is delegating to
// another account.
var DelegationPrefix = []byte{0xef, 0x01, 0x00}

// ParseDelegation tries to parse the address from a delegation slice.
func ParseDelegation(b []byte) (common.Address, bool) {
	if len(b) != 23 || !bytes.HasPrefix(b, DelegationPrefix) {
		return common.Address{}, false
	}
	var addr common.Address
	copy(addr[:], b[len(DelegationPrefix):])
	return addr, true
}

// AddressToDelegation adds the delegation prefix to the specified address.
func AddressToDelegation(addr common.Address) []byte {
	return append(DelegationPrefix, addr.Bytes()...)
}
```

**File:** blockchain/types/tx_internal_data_ethereum_set_code.go (L354-360)
```go
func (t *TxInternalDataEthereumSetCode) Validate(stateDB StateDB, currentBlockNumber uint64, onlyMutableChecks bool) error {
	if !onlyMutableChecks {
		if common.IsPrecompiledContractAddress(t.Recipient, *fork.Rules(big.NewInt(int64(currentBlockNumber)))) {
			return kerrors.ErrPrecompiledContractAddress
		}
	}
	return nil
```

**File:** blockchain/state_transition.go (L732-794)
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

**File:** blockchain/vm/evm.go (L277-289)
```go
	// Filter out invalid precompiled address calls, and create a precompiled contract object if it is not exist.
	// Because IsPrecompiledContractAddress checks for 0..0x400 and ConsoleLog address is outside of the range, we add one more condition if UseConsoleLog.
	if common.IsPrecompiledContractAddress(addr, evm.chainRules) || (addr == consoleLogContractAddress && evm.Config.UseConsoleLog) {
		precompiles := evm.GetPrecompiledContractMap(caller.Address())
		if precompiles[addr] == nil || value.Sign() != 0 {
			// Return an error if an enabled precompiled address is called or a value is transferred to a precompiled address.
			return nil, gas, kerrors.ErrPrecompiledContractAddress
		}
		// create an account object of the enabled precompiled address if not exist.
		if !evm.StateDB.Exist(addr) {
			evm.StateDB.CreateSmartContractAccount(addr, params.CodeFormatEVM, evm.chainRules)
		}
	}
```

**File:** blockchain/vm/evm.go (L561-563)
```go
	if common.IsPrecompiledContractAddress(address, evm.chainRules) {
		return nil, common.Address{}, gas, kerrors.ErrPrecompiledContractAddress
	}
```
