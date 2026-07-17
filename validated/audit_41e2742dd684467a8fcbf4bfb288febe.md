Let me analyze the external bug pattern and search for nearcore analogs. The core issue is: internal accounting says funds are available, but actual balance is depleted by a separate operation, causing withdrawals to fail.

For nearcore, I'll look for cases where internal tracking/accounting says data is available, but the actual data has been moved/consumed/GC'd by another operation.