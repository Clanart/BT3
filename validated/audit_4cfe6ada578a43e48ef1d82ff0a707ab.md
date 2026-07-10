Looking at the vulnerability class — **permanent fund lock due to missing escape hatch when a critical state/mapping is absent** — I need to find an analog in the Omni Bridge where user funds can be irrecoverably locked with no user-callable recovery path.

Let me examine the `init_transfer` flow and `sign_transfer` validation in the NEAR bridge.