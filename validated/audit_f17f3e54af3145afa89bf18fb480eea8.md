[1](#0-0) [2](#0-1)

### Citations

**File:** libraries/SignedSafeMath.sol (L20-23)
```text
    //rounds to zero if x*y < WAD / 2
    function wmul(int256 x, int256 y) internal pure returns (int256) {
        return ((x * y) + (WAD / 2)) / WAD;
    }
```

**File:** wombat/WombatBribeManager.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```
