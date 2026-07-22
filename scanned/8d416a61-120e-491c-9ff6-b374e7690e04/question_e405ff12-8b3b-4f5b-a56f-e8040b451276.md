[File: 'metric-periphery/contracts/extensions/base/BaseMetricExtension.sol -> Scope: Medium. Amount limit, deadline, token ordering, bitmap direction, or extensionData propagation mismatch breaks core swap functionality or user protection.'] [Symbol: MetricOmmSimpleRouter.exactOutput / amountInMax check at tradesLeft==0 only] Can an attacker-controlled pool sequence under a state where intermediate hops consume more input than expected reach _exactOutputIterateCallback -> tradesLeft > 0 branch -> pool.swap(msg.sender, zeroForOne, -amountToPay, ...) -> recursive callback -> tradesLeft==0 -> if (amountIn > cb.amountInMax) revert and violate the invariant that the amountInMaximum cap is enforced before any token is pulled from the payer, corrupting the payer's token balance (tokens pulled before the cap check at the innermost hop) with scoped impact of user paying more than amountInMaximum if the transaction reverts after token pulls at intermediate hops? Proof idea: build a Foundry exactOutput test where an intermediate pool requests more input than amountInMaximum; assert the revert happens after

### Citations

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L1-107)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmExtensions} from
