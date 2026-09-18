### Title
Use of `transfer()` with a fixed 2300 gas stipend in `WSEI.withdraw()` permanently locks native SEI for smart-contract holders - (File: contracts/src/WSEI.sol)

### Summary
`WSEI.sol` is the canonical Wrapped-SEI ERC20 contract shipped as a Sei EVM artifact [1](#0-0) , deployable/referenced through the EVM tx CLI [2](#0-1) . Its `withdraw()` function unwraps WSEI back to native SEI using the deprecated `transfer()` call, which forwards a hard-coded 2300 gas stipend, exactly the bug class described in the external report (`CEther.sol:167: to.transfer(amount)`).

### Finding Description
`withdraw()` debits the caller's WSEI balance and then sends native SEI back to `msg.sender` via `payable(msg.sender).transfer(wad)`: [3](#0-2) 

Because `transfer()` forwards only 2300 gas, any smart-contract account (multisig wallet, account-abstraction wallet, proxy, DeFi vault, etc.) whose `receive()`/`fallback()` needs more than 2300 gas — or whose call path is nested one extra level through a proxy — will have this call revert. `deposit()`/`transfer()`/`transferFrom()` on the WSEI ERC20 token still function normally, so the balance itself is not lost at the accounting layer, but the only code path capable of converting WSEI back into native SEI (`withdraw()`) is permanently unusable for that class of caller. Unlike `MultiSender.sol`, which uses `send()` and checks the boolean return value (allowing callers to detect and handle failure), `WSEI.withdraw()` provides no fallback mechanism (no `call{value:}` alternative, no way for a third party to redeem on the contract's behalf), so the locked SEI has no path to recovery once the WSEI balance is minted to a fallback-incompatible contract address.

### Impact Explanation
Any contract that deposits native SEI into WSEI (directly via `deposit()`/`receive()`, or by receiving WSEI tokens through `transfer`/`transferFrom`) and cannot execute logic within a 2300 gas budget in its receive function will be unable to ever redeem the underlying native SEI locked in the WSEI contract. This is a permanent freezing of funds for the affected contract, satisfying the "permanent freezing" impact bar. Because WSEI is a canonical, protocol-shipped ERC20 wrapper artifact (not merely an example/test contract), any user routing funds through DEXes, vaults, or other contracts that hold WSEI and later call `withdraw()` on behalf of themselves is exposed.

### Likelihood Explanation
Likelihood is significant: many common wallet/vault contracts (Gnosis Safe-style multisigs, ERC-4337 smart accounts, proxies) consume well over 2300 gas in their fallback functions for bookkeeping, and Sei's own contract ecosystem (`BatchCallAndSponsor.sol`, `SimpleAccount7702.sol`, NFT marketplace contracts) already illustrates the prevalence of smart-contract wallets and delegatecall/proxy patterns on the chain, any of which could hold WSEI. This does not require a malicious actor — normal usage by any smart-contract wallet holder triggers the freeze deterministically.

### Recommendation
Replace `payable(msg.sender).transfer(wad)` with a low-level `call` that forwards all available gas and checks the return value, following checks-effects-interactions:
```solidity
(bool success, ) = msg.sender.call{value: wad}("");
require(success, "WSEI: ETH transfer failed");
```
Balance is already decremented before the external call, so CEI ordering is preserved; only the transfer primitive needs to change.

### Proof of Concept
1. Deploy a smart-contract wallet `C` whose `receive()` function consumes more than 2300 gas (e.g., writes to storage or emits a large event) — such patterns are common in production account-abstraction/multisig wallets.
2. From `C`, call `WSEI.deposit{value: X}()` (or otherwise receive WSEI tokens totaling `X`), and later call `WSEI.transfer`/`transferFrom` to accumulate a WSEI balance.
3. From `C`, call `WSEI.withdraw(X)`. `balanceOf[C] -= X` succeeds, but `payable(C).transfer(X)` reverts inside the `receive()` call due to the 2300 gas stipend being insufficient, causing the entire `withdraw()` transaction to revert.
4. `C` now has no way to redeem its WSEI balance for native SEI: `withdraw()` will always revert for this contract, permanently freezing `X` SEI backing that WSEI balance inside the `WSEI` contract, confirmed by `withdraw()`'s single code path shown at [3](#0-2) .

### Citations

**File:** x/evm/artifacts/wsei/artifacts.go (L1-8)
```go
package wsei

import (
	"embed"
	"encoding/hex"
	"strings"

	"github.com/ethereum/go-ethereum/accounts/abi"
```

**File:** x/evm/client/cli/tx.go (L1-4)
```go
package cli

import (
	"context"
```

**File:** contracts/src/WSEI.sol (L27-32)
```text
    function withdraw(uint wad) public {
        require(balanceOf[msg.sender] >= wad);
        balanceOf[msg.sender] -= wad;
        payable(msg.sender).transfer(wad);
        emit Withdrawal(msg.sender, wad);
    }
```
