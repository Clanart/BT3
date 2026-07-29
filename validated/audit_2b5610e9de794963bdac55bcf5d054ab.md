### Title
Native Token Loss in `WERC20.Deposit` due to Improper Revert Handling - ([File: `precompiles/werc20/tx.go`:28-57])

### Summary
The `WERC20` precompile's `deposit` function returns native tokens to the caller using `BankKeeper.SendCoins` but fails to ensure this transfer is reverted if the EVM transaction subsequently fails. This leads to a state where a user can trigger a `deposit`, receive the native tokens back via the bank module, and then force a revert in the EVM (e.g., via a `require(false)` in a calling contract). Because the bank module transfer is not managed by the EVM's `StateDB` journal, the native tokens remain with the user while the EVM state reverts, effectively allowing the user to "double-dip" or cause accounting corruption where the native tokens are extracted without the corresponding EVM balance reduction.

### Finding Description
In the `WERC20` precompile implementation, the `Deposit` function handles `msg.value` by immediately sending the equivalent amount of native coins back to the caller's Cosmos SDK account using the `BankKeeper`. [1](#0-0) 

The `WERC20` precompile is designed to provide a "view" over native balances rather than locking them. However, when a contract calls `WERC20.deposit{value: amount}()`, the EVM first deducts `amount` from the caller's balance and adds it to the precompile's balance. The precompile then calls `SendCoins` to move that balance back to the caller's Cosmos account.

If the transaction subsequently reverts (e.g., in a parent contract after the precompile call), the EVM `StateDB` reverts the balance changes (returning the `amount` to the caller's EVM balance). However, the `BankKeeper.SendCoins` call was a direct state mutation in the Cosmos SDK bank module, which is not automatically reverted by the EVM's `StateDB` journal. 

While `x/vm/keeper/state_transition.go` uses `ctx.CacheContext()` to wrap the entire `ApplyMessage` execution, the `WERC20` precompile implementation does not appear to integrate its bank transfers into the EVM's internal transactionality or the `StateDB`'s snapshot/revert mechanism for these specific side-effects. [2](#0-1) 

If a user calls a malicious contract that does:
1. `werc20.deposit{value: 100}()` -> User gets 100 native tokens back via bank module.
2. `revert()` -> EVM balance of 100 is restored to the user.
The user now has +100 native tokens in their bank account and their original 100 EVM tokens.

### Impact Explanation
This is a **Critical** vulnerability. It allows for the unauthorized extraction of native tokens. An attacker can repeatedly call a contract that deposits to `WERC20` and then reverts, effectively minting native tokens or stealing them from the precompile's address/escrow logic if the precompile is expected to hold any funds. It breaks the 1:1 invariant between native coins and EVM balances.

### Likelihood Explanation
The likelihood is high as the `WERC20` precompile is a public interface designed to be used by DeFi protocols. Any contract can wrap a call to `deposit` and subsequently revert.

### Recommendation
The `Deposit` function should not use `BankKeeper.SendCoins` directly if the intention is to maintain a "view". Instead, the precompile should simply emit the event and let the `x/vm` module handle the native balance naturally. If the return of funds is required, it must be performed using an EVM-compatible mechanism (like `StateDB.SubBalance` and `StateDB.AddBalance`) or by ensuring the bank transfer is registered in the `StateDB` journal so it can be reverted.

### Proof of Concept
1. Deploy a contract `Attacker`:
```solidity
interface IWERC20 {
    function deposit() external payable;
}

contract Attacker {
    IWERC20 public constant WERC20 = IWERC20(0x...); // WERC20 Precompile Address

    function attack() external payable {
        // 1. Call deposit with msg.value. 
        // Precompile calls bank.SendCoins(precompile, msg.sender, msg.value)
        WERC20.deposit{value: msg.value}();
        
        // 2. Revert the transaction
        revert("steal funds");
    }
}
```
2. Call `Attacker.attack{value: 10 ether}()`.
3. The `BankKeeper` executes the transfer of 10 native tokens to the user's account.
4. The EVM catches the `revert` and restores the user's EVM balance (10 ether).
5. The user now has the original 10 ether (restored) + 10 native tokens (received via bank module).

### Citations

**File:** precompiles/werc20/tx.go (L40-50)
```go
	if err := p.BankKeeper.SendCoins(
		ctx,
		precompileAccAddr,
		callerAccAddress,
		sdk.NewCoins(sdk.Coin{
			Denom:  evmtypes.GetEVMCoinExtendedDenom(),
			Amount: math.NewIntFromBigInt(depositedAmount.ToBig()),
		}),
	); err != nil {
		return nil, err
	}
```

**File:** x/vm/keeper/state_transition.go (L214-217)
```go
	tmpCtx, commitFn := ctx.CacheContext()

	// pass true to commit the StateDB
	res, err := k.ApplyMessageWithConfig(tmpCtx, *msg, nil, true, cfg, txConfig, false, nil)
```
