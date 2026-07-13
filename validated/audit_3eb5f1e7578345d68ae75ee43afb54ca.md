### Title
Non-EVM-Native Token Balances Orphaned on Self-Destruct Enable Theft via CREATE2 Redeploy — (`x/evm/statedb/statedb.go`)

### Summary

When a contract self-destructs, `StateDB.Commit()` only burns the EVM-denom balance of the destroyed account. Non-EVM-native tokens (IBC coins, CosmWasm bridge tokens) held by the contract address are explicitly left as orphaned Cosmos bank balances. An attacker who can predict the CREATE2 address can redeploy a new contract at the same address in a subsequent transaction and drain those orphaned tokens, constituting unauthorized theft of Cosmos bank funds.

### Finding Description

In `StateDB.Commit()`, the self-destruct handling path reads:

```go
// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
// bridge) held by the destroyed address are not drained and may remain as
// orphaned bank balances.
if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
    coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
    }
}
if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
    return errorsmod.Wrap(err, "failed to delete account")
}
writeCache()
``` [1](#0-0) 

The `keeper.GetBalance` call is scoped exclusively to `s.evmDenom`. `keeper.DeleteAccount` removes the auth/account metadata and EVM storage, but it never touches the Cosmos bank module balances for any other denom. Any IBC or bridge token balance sitting in the bank module under the destroyed address survives the commit intact.

The `SelfDestruct` function itself only burns the EVM-denom balance at destruction time via `SubBalance`:

```go
balance := s.GetBalance(addr)
if balance.Sign() > 0 {
    s.SubBalance(addr, balance, tracing.BalanceDecreaseSelfdestructBurn)
}
``` [2](#0-1) 

`GetBalance` is also scoped to `s.evmDenom` only:

```go
func (s *StateDB) GetBalance(addr common.Address) *uint256.Int {
    balance := s.keeper.GetBalance(s.ctx, sdk.AccAddress(addr.Bytes()), s.evmDenom)
    return &balance
}
``` [3](#0-2) 

There is no code path anywhere in `SelfDestruct`, `SelfDestruct6780`, or `Commit` that iterates over all bank denoms held by the destroyed address and burns or transfers them.

### Impact Explanation

**Critical — Unauthorized theft of Cosmos bank funds through stateDB commit logic.**

Attack path:
1. Attacker deploys a factory contract and uses CREATE2 to deploy a child contract at a deterministic address.
2. The child contract accumulates non-EVM-native tokens (e.g., IBC ATOM, OSMO) via a precompile or native action that deposits Cosmos bank coins to the contract's address.
3. In the same transaction (satisfying EIP-6780), the attacker calls `SELFDESTRUCT` on the child. The EVM denom balance is burned; IBC tokens remain in the bank module at the child's address.
4. In a subsequent transaction, the attacker redeploys a new contract at the identical CREATE2 address.
5. The new contract's Cosmos bank balance now includes the orphaned IBC tokens. The attacker drains them.

The orphaned tokens are not recoverable by the original depositors and are fully accessible to whoever controls the redeployed contract. The `SelfDestructExploitFactory` test contract in the repository already demonstrates the structural pattern for this attack (CREATE2 deploy → destroy → redeploy). [4](#0-3) 

### Likelihood Explanation

**Medium.** The preconditions are:
- A contract must hold non-EVM-native tokens. This is realistic wherever IBC precompiles or native-action bridges allow Cosmos bank coins to be credited to EVM contract addresses.
- The contract must self-destruct. Under EIP-6780 this requires creation and destruction in the same transaction, which is exactly the pattern the existing exploit factory demonstrates.
- The attacker must predict the CREATE2 address, which is trivially computable off-chain.

All three conditions are reachable by an unprivileged Ethereum transaction with no governance or validator cooperation required.

### Recommendation

In `StateDB.Commit()`, after burning the EVM-denom balance and before calling `DeleteAccount`, iterate over **all** bank module balances held by the destroyed address and burn them (or transfer them to a designated recovery address):

```go
// Burn all remaining bank balances, not just the EVM denom.
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
for _, coin := range allBalances {
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrapf(err, "failed to burn post-selfdestruct %s balance", coin.Denom)
    }
}
```

Alternatively, transfer non-EVM-native balances to the SELFDESTRUCT beneficiary address (the address passed to the opcode), matching Ethereum's intent of forwarding remaining value to the beneficiary.

### Proof of Concept

```
Tx 1 (single transaction):
  factory.attackInOneTx(salt, {value: 0})
    → CREATE2 deploys child at predictable address A
    → IBC precompile credits 1000 ATOM to address A (bank module)
    → child.SELFDESTRUCT()
      - EVM denom at A: burned ✓
      - ATOM at A in bank module: NOT burned ✗ (orphaned)
    → child account deleted from auth module

Tx 2:
  factory.redeployChild(salt)
    → CREATE2 redeploys new contract at address A
    → new contract's bank balance: 1000 ATOM (inherited from orphaned balance)
    → attacker drains 1000 ATOM
```

The code comment at the burn site explicitly acknowledges this gap:

> "Non-EVM-native tokens (IBC, CosmWasm bridge) held by the destroyed address are not drained and may remain as orphaned bank balances." [5](#0-4)

### Citations

**File:** x/evm/statedb/statedb.go (L210-213)
```go
func (s *StateDB) GetBalance(addr common.Address) *uint256.Int {
	balance := s.keeper.GetBalance(s.ctx, sdk.AccAddress(addr.Bytes()), s.evmDenom)
	return &balance
}
```

**File:** x/evm/statedb/statedb.go (L570-574)
```go
	// clear balance
	balance := s.GetBalance(addr)
	if balance.Sign() > 0 {
		s.SubBalance(addr, balance, tracing.BalanceDecreaseSelfdestructBurn)
	}
```

**File:** x/evm/statedb/statedb.go (L811-825)
```go
			cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
			cacheCtx, writeCache := s.origCtx.CacheContext()
			// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
			// bridge) held by the destroyed address are not drained and may remain as
			// orphaned bank balances.
			if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
				coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
				if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
					return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
				}
			}
			if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
				return errorsmod.Wrap(err, "failed to delete account")
			}
			writeCache()
```

**File:** tests/integration_tests/hardhat/contracts/SelfDestructExploit.sol (L54-65)
```text
    function attackInOneTx(bytes32 salt) external payable returns (address childAddr) {
        bytes memory initCode = targetInitCode;
        assembly {
            childAddr := create2(0, add(initCode, 0x20), mload(initCode), salt)
        }
        require(childAddr != address(0), "Create2 deployment failed");

        SelfDestructTarget(payable(childAddr)).destroy();

        (bool ok, ) = childAddr.call{value: msg.value}("");
        require(ok, "Post-selfdestruct value transfer failed");
    }
```
