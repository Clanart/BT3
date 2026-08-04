This confirms the vulnerability is real and reaches production deployment paths, not just tests.

Key facts:
- `HyperFungibleTokenImpl.sol` is deployed directly in the non-mainnet branch of the production deployment script `evm/script/DeployIsmp.s.sol:100-108`, where it's used as the actual `feeToken` for the bridge, with `TokenFaucet` granted minter role.
- `superApprove` has **no access control whatsoever** — no `onlyRole`, no `msg.sender` check, no restriction tying it to `owner` — it lets any caller set `_approve(owner, spender, type(uint256).max)` for **any arbitrary owner and spender pair**.
- `TokenFaucet.drip` (`evm/src/utils/TokenFaucet.sol:29-39`) mints tokens directly to `msg.sender`, so an attacker can pre-approve themselves against any future faucet recipient's address before that recipient calls `drip`, then drain the recipient's balance via `transferFrom` once tokens land.

This isn't scoped only to the faucet interaction — since `superApprove` has zero access control, anyone can drain **any** balance of this token from **any** holder at any time, not just faucet recipients. The faucet scenario is simply one demonstration of the broader unrestricted-approval bug.

### Title
Unrestricted `superApprove` allows arbitrary theft of any `HyperFungibleTokenImpl` balance, including faucet-distributed funds - (File: `evm/src/utils/HyperFungibleTokenImpl.sol`)

### Summary
`HyperFungibleTokenImpl.superApprove(address owner, address spender)` is a `public` function with no access control that calls the internal `_approve(owner, spender, type(uint256).max)`, letting **any unprivileged caller** grant an unlimited ERC20 allowance from **any owner** to **any spender**. Since `TokenFaucet.drip` mints tokens to `msg.sender` and this token contract is deployed as the live fee token in the non-mainnet deployment path, an attacker can pre-position an unlimited allowance from a soon-to-be faucet recipient to themselves, then call `transferFrom` to steal freshly minted tokens the instant they're distributed. More broadly, the same primitive lets an attacker drain any holder's balance at will, at any time. [1](#0-0) 

### Finding Description
`superApprove` is meant as a "helper function for tests" per its docstring, but it lives in the production `evm/src/utils/HyperFungibleTokenImpl.sol` contract with no guard: [1](#0-0) . Unlike `mint`/`burn`, which are gated by `onlyRole(MINTER_ROLE)`/`onlyRole(BURNER_ROLE)` [2](#0-1) , `superApprove` has no modifier at all and takes `owner` as an arbitrary parameter rather than deriving it from `msg.sender`.

This contract is not confined to tests — the production deployment script instantiates it directly as the live bridge fee token when not deploying on mainnet, and wires `TokenFaucet` with `MINTER_ROLE` against it: [3](#0-2) . `TokenFaucet.drip` mints tokens directly to `msg.sender` on a daily cadence: [4](#0-3) .

An attacker can:
1. Call `superApprove(victim, attacker)` for any address `victim` they expect to request faucet funds (or any existing holder), at zero cost and with zero privilege.
2. Once `victim` calls `drip(token)` and receives 1000 tokens, or whenever `victim` otherwise holds a balance, the attacker calls `transferFrom(victim, attacker, amount)` to drain it immediately, since the max allowance was already set.

### Impact Explanation
This allows unauthorized transaction execution and wrongful, unauthorized asset movement of any holder's balance in this token, not merely faucet-distributed amounts — satisfying "stealing or loss of funds" and "unauthorized transaction or execution" under the impact gate. Because this token is deployed as the actual fee token backing bridge operations in non-mainnet deployments (used to pay for ISMP dispatch fees, staking, etc. per the surrounding test/deployment wiring), draining balances can also disrupt fee payment and bridging operations for legitimate users.

### Likelihood Explanation
Trivial and fully unprivileged: the function is `public`, requires no role, no signature, and no prior relationship between attacker and victim. Any address can call it directly on-chain against any other address, at any time, including proactively targeting addresses about to receive faucet drips.

### Recommendation
Remove `superApprove` from the production contract entirely, or if a test helper is genuinely required, move it to a test-only mock contract that is never deployed in `DeployIsmp.s.sol` or any production path. If retained in production for some purpose, restrict it so it can only be called by `msg.sender` acting as its own `owner` (i.e., treat it as a convenience wrapper around standard `approve`), and never allow an arbitrary third party to set another address's allowance.

### Proof of Concept
```solidity
// Using DeployIsmp.s.sol's non-mainnet deployment:
// feeTokenInstance = HyperFungibleTokenImpl, faucet = TokenFaucet with MINTER_ROLE

address victim = makeAddr("victim");
address attacker = makeAddr("attacker");

// Attacker pre-positions unlimited allowance on the victim's future balance,
// with zero privilege and no interaction from victim required.
vm.prank(attacker);
feeTokenInstance.superApprove(victim, attacker);

// Victim requests faucet funds as normal.
vm.prank(victim);
faucet.drip(address(feeTokenInstance));
assertEq(feeTokenInstance.balanceOf(victim), 1000 * 1e18);

// Attacker immediately drains the victim's freshly-minted balance.
vm.prank(attacker);
feeTokenInstance.transferFrom(victim, attacker, 1000 * 1e18);

assertEq(feeTokenInstance.balanceOf(victim), 0);
assertEq(feeTokenInstance.balanceOf(attacker), 1000 * 1e18);
```

### Citations

**File:** evm/src/utils/HyperFungibleTokenImpl.sol (L64-76)
```text
    function mint(address to, uint256 amount) external onlyRole(MINTER_ROLE) {
        _mint(to, amount);
    }

    /**
     * @notice Burns tokens from the specified account
     * @dev Can be called by any address with BURNER_ROLE
     * @param from The address from which tokens will be burned
     * @param amount The amount of tokens to burn
     */
    function burn(address from, uint256 amount) external onlyRole(BURNER_ROLE) {
        _burn(from, amount);
    }
```

**File:** evm/src/utils/HyperFungibleTokenImpl.sol (L110-117)
```text
    /**
     * @notice Helper function for tests - approves unlimited tokens
     * @param owner The owner of the tokens
     * @param spender The spender address
     */
    function superApprove(address owner, address spender) public {
        _approve(owner, spender, type(uint256).max);
    }
```

**File:** evm/script/DeployIsmp.s.sol (L100-108)
```text
            // Deploy our own feetoken contract & faucet
            faucet = new TokenFaucet{salt: salt}();
            feeTokenInstance = new HyperFungibleTokenImpl{salt: salt}(admin, "Hyper USD", "USD.h");
            // Grant minter role to faucet so it can mint tokens
            feeTokenInstance.grantMinterRole(address(faucet));
            feeToken = address(feeTokenInstance);
            hyperbridge = StateMachine.kusama(paraId);
            decimals = 18;
        }
```

**File:** evm/src/utils/TokenFaucet.sol (L29-39)
```text
    function drip(address token) public {
        uint256 lastDrip = consumers[msg.sender];
        uint256 delay = block.timestamp - lastDrip;

        if (delay < 1 days) {
            revert("Can only request tokens once daily");
        }

        consumers[msg.sender] = block.timestamp;
        HyperFungibleTokenImpl(token).mint(msg.sender, 1000 * 1e18);
    }
```
