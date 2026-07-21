Looking at the codebase, I need to find a sequencer-native analog to the "pending allowances can be exploited" pattern: a function that consumes a pending authorization state without verifying the caller is the actual owner of that state.

Let me examine the `skip_stateful_validations` function and the `account_tx_in_pool_or_recent_block` check more carefully.