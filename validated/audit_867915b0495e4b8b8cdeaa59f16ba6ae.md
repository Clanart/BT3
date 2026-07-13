### Title
Non-EVM-Denom Bank Balances Permanently Orphaned After Contract Self-Destruct — (File: x/evm/statedb/statedb.go)

---

### Summary

When an EVM contract self-destructs, Ethermint's `StateDB.Commit()` burns only the EVM-native denom balance and deletes the account metadata. Non-EVM-denom tokens (IBC assets, bridged tokens) held by the contract address in the Cosmos bank module are never drained and remain permanently locked under the now-deleted account address, with no recovery path.

---

### Finding Description

The vulnerability class from the reference report is: **funds transferred to a contract through an external/bypass path that skips the normal accounting mechanism, causing permanent lock-up.** The direct analog in Ethermint is the self-destruct path in `StateDB.Commit()`.

In `x/evm/statedb/statedb.go`, the `Commit()` function processes self-destructed accounts as follows: [1](#0-0) 

The code explicitly burns only the EVM denom:

```go
// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
// bridge) held by the destroyed address are not drained and may remain as
// orphaned bank balances.
if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
    coin := sdk.NewCoin(s.evmDenom, ...)
    s.keeper.SubBalance(cacheCtx, cosmosAddr, coin)
}
if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil { ... }
writeCache()
```

`DeleteAccount` removes the auth-module account record (code hash, nonce, sequence), but the Cosmos bank module stores balances independently of account objects. Any non-EVM-denom balance (IBC USDC, stATOM, bridged tokens deposited via a bank or IBC precompile) remains in the bank under the contract's address after deletion.

The parallel to the reference bug is exact:

| Reference (andromeda) | Ethermint analog |
|---|---|
| Validator kicked → `x/staking` transfers funds directly to contract, bypassing `UNSTAKING_QUEUE` | Contract self-destructs → bank module retains non-EVM-denom balances, bypassing the burn step in `Commit()` |
| Funds stuck in contract with no withdrawal path | Non-EVM tokens stuck under deleted account with no recovery path |

The `SelfDestruct` function itself only burns the EVM denom balance at destruction time: [2](#0-1) 

And the `Commit()` post-destruction burn also only covers the EVM denom: [3](#0-2) 

After `DeleteAccount`, the address has no code, no nonce, and no private key. The bank balances for non-EVM denoms are permanently inaccessible.

---

### Impact Explanation

Non-EVM-denom tokens (IBC assets, bridged tokens) deposited into an EVM contract are permanently lost when that contract self-destructs. The bank module retains the balance under the deleted account address, but:

- The address has no private key (it is a contract address derived from `CREATE`/`CREATE2`).
- The auth-module account is deleted, so no Cosmos transaction can be signed from it.
- There is no on-chain mechanism to recover these orphaned balances.

This constitutes **permanent, irrecoverable fund loss** for users who deposited non-EVM-denom tokens into the contract — matching the High impact category: *"EVM state transition bug that permits valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

The scenario is reachable by any unprivileged user on chains that deploy IBC or bank precompiles (which are common in production Cosmos EVM deployments). The attacker-controlled entry path is:

1. Deploy a contract that accepts non-EVM-denom tokens (e.g., via an IBC transfer precompile or `x/bank` precompile).
2. Deposit non-EVM-denom tokens into the contract (legitimate user action or attacker-controlled).
3. Trigger `SELFDESTRUCT` on the contract (via a public function, a reentrancy exploit, or EIP-6780 conditions).
4. `StateDB.Commit()` burns only the EVM denom; non-EVM-denom tokens remain orphaned.

No privileged role is required. The attacker only needs to control a contract with a self-destruct path and the ability to deposit non-EVM-denom tokens into it.

---

### Recommendation

In `StateDB.Commit()`, when processing a `selfDestructed` account, enumerate **all** bank balances for the address (not just the EVM denom) and burn or redirect them before calling `DeleteAccount`:

```go
// Drain ALL bank balances, not just the EVM denom
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
for _, coin := range allBalances {
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrap(err, "failed to burn post-selfdestruct non-evm balance")
    }
}
```

Alternatively, transfer orphaned non-EVM-denom balances to a designated recovery module account rather than burning them.

---

### Proof of Concept

1. Chain has an IBC transfer precompile at address `0xIBC`.
2. Attacker deploys contract `C` with:
   ```solidity
   function deposit() external { /* accepts IBC USDC via precompile */ }
   function destroy() external { selfdestruct(payable(msg.sender)); }
   ```
3. Users deposit 10,000 IBC USDC into `C` via `deposit()`.
4. Attacker calls `C.destroy()`.
5. `StateDB.SelfDestruct(C)` burns `C`'s EVM denom balance.
6. `StateDB.Commit()` burns any remaining EVM denom balance, calls `DeleteAccount(C)`.
7. The 10,000 IBC USDC remains in `bankKeeper` under `C`'s address.
8. `C`'s auth account is deleted — no code, no nonce, no private key.
9. The 10,000 IBC USDC is permanently inaccessible.

The root cause is the explicit bypass at: [4](#0-3) 

where only `s.evmDenom` is checked and burned, leaving all other bank module balances unhandled.

### Citations

**File:** x/evm/statedb/statedb.go (L557-576)
```go
func (s *StateDB) SelfDestruct(addr common.Address) uint256.Int {
	stateObject := s.getStateObject(addr)
	var prevBalance uint256.Int
	if stateObject == nil {
		return prevBalance
	}
	prevBalance = *(stateObject.Balance())
	s.journal.append(selfDestructChange{
		account:     &addr,
		prev:        stateObject.selfDestructed,
		prevbalance: new(uint256.Int).Set(&prevBalance),
	})
	stateObject.markSelfDestructed()
	// clear balance
	balance := s.GetBalance(addr)
	if balance.Sign() > 0 {
		s.SubBalance(addr, balance, tracing.BalanceDecreaseSelfdestructBurn)
	}
	return prevBalance
}
```

**File:** x/evm/statedb/statedb.go (L800-825)
```go
		if obj.selfDestructed {
			// Burn any balance that arrived after SelfDestruct was called (e.g., via a
			// value-bearing CALL to the destroyed address within the same transaction).
			// SelfDestruct already burned the balance present at destruction time, but
			// subsequent AddBalance calls write to the bank without a matching burn.
			// DeleteAccount only removes auth metadata and storage; it never touches the
			// bank balance, so we must drain it here before removing the account.
			//
			// Both operations run inside a single CacheContext so that if DeleteAccount
			// fails after SubBalance, the partial burn is rolled back and the bank is
			// left consistent.
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
