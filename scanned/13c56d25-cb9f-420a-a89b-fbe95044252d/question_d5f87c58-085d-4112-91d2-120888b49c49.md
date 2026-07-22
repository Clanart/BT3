[File: 'metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol -> Scope: Critical. Exact-output recursion or path handling causes router to overpay input, underdeliver output, or settle against the wrong pool/token pair.'] [Symbol: MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback] Can attacker-controlled KIND_BYTE under CALLBACK_DECODE_CONTEXT reach metricOmmModifyLiquidityCallback -> abi.decode(callback

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L1-246)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPoolActions} from
